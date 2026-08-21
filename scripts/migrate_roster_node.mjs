import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';

const root = path.resolve(import.meta.dirname, '..');
const source = path.resolve(process.argv[2] || path.join(root, '..', 'outputs', 'athletic-pe-roster-data.js'));
const instance = path.resolve(process.env.ARC_INSTANCE_PATH || path.join(root, 'instance'));
const databasePath = path.resolve(process.env.ARC_DATABASE_PATH || path.join(instance, 'arc-strength.sqlite3'));
fs.mkdirSync(instance, {recursive: true});
if (process.argv.includes('--replace-generated')) {
  if (!instance.startsWith(root + path.sep)) throw new Error('Refusing to replace files outside the project');
  for (const name of ['arc-strength.sqlite3','arc-strength.sqlite3-wal','arc-strength.sqlite3-shm','master.key','lookup.key','flask-secret.key','setup-token']) {
    fs.rmSync(path.join(instance, name), {force: true});
  }
}
if (fs.existsSync(databasePath)) throw new Error(`Refusing to overwrite ${databasePath}`);

function privateFile(name, value) {
  const target = path.join(instance, name);
  fs.writeFileSync(target, value, {encoding: 'utf8', mode: 0o600, flag: 'wx'});
  return value;
}
const urlsafeBase64 = buffer => buffer.toString('base64').replace(/\+/g, '-').replace(/\//g, '_');
const fernetText = privateFile('master.key', urlsafeBase64(crypto.randomBytes(32)));
const lookupText = privateFile('lookup.key', crypto.randomBytes(32).toString('hex'));
privateFile('flask-secret.key', crypto.randomBytes(64).toString('hex'));
privateFile('setup-token', crypto.randomBytes(32).toString('base64url'));
const fernetKey = Buffer.from(fernetText, 'base64url');
const signingKey = fernetKey.subarray(0, 16), encryptionKey = fernetKey.subarray(16);
const lookupKey = Buffer.from(lookupText, 'utf8');

function encrypt(value) {
  if (value === null || value === undefined) return null;
  const iv = crypto.randomBytes(16), cipher = crypto.createCipheriv('aes-128-cbc', encryptionKey, iv);
  const plaintext = Buffer.from(String(value), 'utf8');
  const ciphertext = Buffer.concat([cipher.update(plaintext), cipher.final()]);
  const timestamp = Buffer.alloc(8); timestamp.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 1000)));
  const body = Buffer.concat([Buffer.from([0x80]), timestamp, iv, ciphertext]);
  const signature = crypto.createHmac('sha256', signingKey).update(body).digest();
  return Buffer.from(urlsafeBase64(Buffer.concat([body, signature])));
}
function lookup(value) { return crypto.createHmac('sha256', lookupKey).update(String(value).trim().toLocaleLowerCase()).digest('hex'); }

const seedText = fs.readFileSync(source, 'utf8');
const match = seedText.match(/window\.ATHLETIC_PE_ROSTER\s*=\s*(\{[\s\S]*\})\s*;?\s*$/);
if (!match) throw new Error('Roster seed format was not recognized');
const seed = JSON.parse(match[1]);
const dbSource = fs.readFileSync(path.join(root, 'db.py'), 'utf8');
const schemaMatch = dbSource.match(/SCHEMA = """([\s\S]*?)"""/);
if (!schemaMatch) throw new Error('Database schema not found');
const db = new DatabaseSync(databasePath);
db.exec(schemaMatch[1]);
const now = new Date().toISOString();
const groups = ['Football', 'Nonfootball Group A', 'Nonfootball Group B'];
const classify = athlete => athlete.sports?.includes('Football') ? 'Football' : athlete.sports?.some(s => ['Baseball','Soccer'].includes(s)) ? 'Nonfootball Group A' : 'Nonfootball Group B';
const insertGroup = db.prepare('INSERT INTO class_groups(name,sort_order) VALUES(?,?)');
groups.forEach((name, index) => insertGroup.run(name, index));
const insertSport = db.prepare('INSERT INTO sports(name,sort_order) VALUES(?,?)');
seed.sports.forEach((name, index) => insertSport.run(name, index));
const sportRows = db.prepare('SELECT id,name FROM sports').all(), sportIds = Object.fromEntries(sportRows.map(r => [r.name, r.id]));
const groupRows = db.prepare('SELECT id,name FROM class_groups').all(), groupIds = Object.fromEntries(groupRows.map(r => [r.name, r.id]));
const insertSportGroup = db.prepare('INSERT OR IGNORE INTO sport_groups(sport_id,name) VALUES(?,?)');
for (const [sport, names] of Object.entries(seed.sportGroups || {})) for (const name of names) insertSportGroup.run(sportIds[sport], name);
const insertAthlete = db.prepare(`INSERT INTO athletes(id,name_enc,name_lookup,grade_enc,teacher_enc,class_group_id,subgroup_enc,maxes_enc,overrides_enc,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)`);
const insertAthleteSport = db.prepare('INSERT INTO athlete_sports(athlete_id,sport_id,subgroup_enc) VALUES(?,?,?)');
db.exec('BEGIN IMMEDIATE');
try {
  seed.athletes.forEach((athlete, index) => {
    const id = athlete.id || `athlete-${String(index + 1).padStart(4, '0')}`, group = classify(athlete);
    insertAthlete.run(id, encrypt(athlete.name), lookup(athlete.name), encrypt(athlete.grade || ''), encrypt(athlete.teacher || ''), groupIds[group], encrypt(athlete.subgroup || ''), encrypt(JSON.stringify(athlete.maxes || {})), encrypt(JSON.stringify(athlete.overrides || {})), now, now);
    [...new Set(athlete.sports || [])].forEach(sport => insertAthleteSport.run(id, sportIds[sport], encrypt(athlete.groupBySport?.[sport] || '')));
  });
  db.prepare('INSERT INTO app_settings(setting_key,value_enc,updated_at) VALUES(?,?,?)').run('liftLibrary', encrypt(JSON.stringify(['Bench','Back Squat','Power Clean','Deadlift','Front Squat'])), now);
  db.exec('COMMIT');
} catch (error) { db.exec('ROLLBACK'); throw error; }
db.exec('PRAGMA optimize');
const counts = Object.fromEntries(groups.map(group => [group, seed.athletes.filter(a => classify(a) === group).length]));
console.log(`Migrated ${seed.athletes.length} encrypted athlete records to ${databasePath}`);
console.log(JSON.stringify(counts));
