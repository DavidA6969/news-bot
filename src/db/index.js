import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import Database from 'better-sqlite3';
import config from '../config.js';

const here = path.dirname(fileURLToPath(import.meta.url));

function open(file) {
  const db = new Database(file);
  db.pragma('foreign_keys = ON');
  db.exec(fs.readFileSync(path.join(here, 'schema.sql'), 'utf8'));
  return db;
}

fs.mkdirSync(config.dataDir, { recursive: true });

export const db = open(path.join(config.dataDir, 'launchkit.sqlite'));

/** Build an in-memory database — used by the test suite. */
export function createMemoryDb() {
  return open(':memory:');
}

export default db;
