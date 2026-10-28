// Usage: npm run pipeline -- <roleId> [--llm-gate]
// Runs Oscar -> Remi -> Jules -> Committee -> Gaston for one role, from the terminal, exactly like the
// "Run full pipeline" button. The server does not need to be running (stop it first if it is, so the
// two don't write the same files at once).
import '../env.js';
import { getRole, listRoles, saveRoles } from '../rolesStore.js';
import { pipelineProblem, runPipeline } from '../pipeline.js';

const [id, ...flags] = process.argv.slice(2);
const role = getRole(id || '');
if (!role) {
    console.log('Usage: npm run pipeline -- <roleId> [--llm-gate]\nRoles: ' +
        (listRoles().map(r => `${r.id} (${r.title})`).join(', ') || 'none yet'));
    process.exit(1);
}
const problem = pipelineProblem(role);
if (problem) {
    console.error(problem);
    process.exit(1);
}
console.log(`Running the pipeline for ${role.id} (${role.title}) with LLM_BACKEND=${process.env.LLM_BACKEND || 'claude_cli'} ...`);
role.agents ||= {};
role.agents.pipeline = { state: 'running', startedAt: new Date().toISOString(), message: '' };
saveRoles();
try {
    const msg = await runPipeline(role, { llmGate: flags.includes('--llm-gate') });
    Object.assign(role.agents.pipeline, { state: 'done', message: msg });
    console.log(msg);
    console.log(`Report: data/roles/${role.id}/report.md`);
} catch (e) {
    Object.assign(role.agents.pipeline, { state: 'failed', message: e.message });
    console.error(e.message);
    process.exitCode = 1;
} finally {
    role.agents.pipeline.finishedAt = new Date().toISOString();
    saveRoles();
}
