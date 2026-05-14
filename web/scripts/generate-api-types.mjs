import { execFileSync } from 'node:child_process';
import { existsSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..', '..');
const webRoot = resolve(here, '..');
const openapiPath = resolve(webRoot, 'src', 'api', 'openapi.json');
const schemaPath = resolve(webRoot, 'src', 'api', 'schema.d.ts');
const python = existsSync(resolve(root, '.venv', 'bin', 'python'))
  ? resolve(root, '.venv', 'bin', 'python')
  : 'python3.11';

const code = [
  'import json',
  'from petition_verifier.api import app',
  'print(json.dumps(app.openapi()))',
].join('; ');

const openapi = execFileSync(python, ['-c', code], {
  cwd: root,
  env: {
    ...process.env,
    PYTHONPATH: resolve(root, 'src'),
    PETITION_VERIFIER_SKIP_DB_INIT: 'true',
    SECRET_KEY: process.env.SECRET_KEY || 'openapi-generation-only',
  },
  encoding: 'utf8',
});

writeFileSync(openapiPath, openapi);
execFileSync(
  resolve(webRoot, 'node_modules', '.bin', 'openapi-typescript'),
  [openapiPath, '-o', schemaPath],
  { cwd: webRoot, stdio: 'inherit' },
);
rmSync(openapiPath, { force: true });
