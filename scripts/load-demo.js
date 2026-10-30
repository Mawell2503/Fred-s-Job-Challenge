// Usage: npm run demo
// Creates the demo role (demo/role.json) and submits the 15 fictional applications in demo/
// for it, through the same code as the website (Oscar runs on every CV). They join the CV pool;
// run again, the same CVs are not added twice (they are only marked as applying for the new role). Stop the server first so the
// two don't write data/*.json at the same time. Then: npm run pipeline -- <the role id printed below>
import '../env.js';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { createRole, saveRoles } from '../rolesStore.js';
import { cleanJob } from '../rolesStore.js';
import { cleanLogistics, cleanPractical } from '../practical.js';
import { UPLOAD_DIR, submitApplication } from '../applicants.js';
import { batchNamed } from '../batches.js';

const DEMO = path.resolve('demo');
const roleSpec = JSON.parse(fs.readFileSync(path.join(DEMO, 'role.json'), 'utf8'));
const people = JSON.parse(fs.readFileSync(path.join(DEMO, 'candidates.json'), 'utf8'));

const role = createRole(roleSpec.title);
const demoBatch = batchNamed('Demo applications');   // the 15 demo CVs form one batch
role.adText = roleSpec.adText;
role.job = cleanJob(roleSpec.job, role.title);
role.jobUpdatedAt = new Date().toISOString();
role.logistics = cleanLogistics(roleSpec.logistics);
saveRoles();
console.log(`Created ${role.id}: ${role.title}`);

// a placeholder "LinkedIn PDF" (the LinkedIn parser is not built yet; only its presence matters today)
const placeholderPdf = name => Buffer.from(`%PDF-1.4\n% Demo placeholder for ${name}'s LinkedIn profile (fictional)\n%%EOF\n`);

for (const p of people) {
    const cvPath = path.join(UPLOAD_DIR, crypto.randomUUID() + path.extname(p.cv));
    fs.copyFileSync(path.join(DEMO, 'cvs', p.cv), cvPath);
    let linkedinPath = null;
    if (p.linkedin) {
        linkedinPath = path.join(UPLOAD_DIR, crypto.randomUUID() + '.pdf');
        fs.writeFileSync(linkedinPath, placeholderPdf(p.name));
    }
    const practical = cleanPractical(p.practical, role);
    if (practical.error) throw new Error(`${p.name}: ${practical.error}`);
    const out = await submitApplication({ roleId: role.id, cvPath, cvName: p.cv, linkedinPath, name: p.name,
        practical: practical.value, source: 'demo', batchId: demoBatch.id });
    if (out.duplicate || out.added || out.restored || out.moved) {
        for (const f of [cvPath, linkedinPath]) if (f) fs.rmSync(f, { force: true });
        console.log(`  - ${p.name}: already in the CV pool${out.added || out.moved || out.restored ? ', now also applying for this role' : ', skipped'}`);
        continue;
    }
    if (!out.applicant) {
        console.log(`  - ${p.name}: the CV could not be read, not recorded`);
        continue;
    }
    console.log(`  ${out.applicant.candidateId}  ${p.name.padEnd(18)} CV ${out.result.status.padEnd(7)} ${p.why}`);
}
console.log(`\nNext: npm run pipeline -- ${role.id}   (or open the admin page and press "Run full pipeline")`);
