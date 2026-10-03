// Run inside n8n; write a short-lived import file without printing its token.
const fs = require('fs');
const {execFileSync} = require('child_process');
const token = process.env.TENAZAS_JOB_RUNNER_TOKEN;
if (!token || ['change-me', 'replace-me', 'tenazas-local-job-runner'].includes(token)) {
  throw new Error('A configured runner token is required');
}
const file = '/tmp/tenazas-runner-credentials.json';
try {
  fs.writeFileSync(file, JSON.stringify([{
    id: 'tenazas-job-runner-auth', name: 'Tenazas Job Runner', type: 'httpHeaderAuth',
    data: {name: 'X-Tenazas-Job-Token', value: token}
  }]), {mode: 0o600});
  execFileSync('n8n', ['import:credentials', '--input=' + file], {stdio: 'pipe'});
  console.log('Runner credential imported');
} finally {
  if (fs.existsSync(file)) fs.unlinkSync(file);
}
