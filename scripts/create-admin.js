// Usage: npm run create-admin -- <username> <password>
//        npm run create-admin -- --list
import { createUser, listUsers } from '../auth.js';

const [a, b] = process.argv.slice(2);

if (a === '--list') {
    console.table(listUsers());
} else if (!a || !b) {
    console.log('Usage: npm run create-admin -- <username> <password>\n       npm run create-admin -- --list');
    process.exitCode = 1;
} else {
    const result = await createUser(a, b);
    if (result.error) {
        console.error(result.error);
        process.exitCode = 1;
    } else {
        console.log(`Admin "${result.user.user}" created.`);
    }
}
