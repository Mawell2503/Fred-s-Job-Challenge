// The website always runs from its own folder, wherever it is started from (`node /path/to/index.js`,
// an IDE, a process manager): data/, .env and `python -m cv_reader` / `python -m hiring` are all found
// relative to the working directory. Imported FIRST by index.js, before anything reads a path.
// (The npm scripts already run from here; the tests import the modules directly from a scratch folder.)
import path from 'node:path';
import { fileURLToPath } from 'node:url';

process.chdir(path.dirname(fileURLToPath(import.meta.url)));
