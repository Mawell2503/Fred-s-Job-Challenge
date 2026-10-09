import express from 'express';
import fs from 'node:fs';

const app = express();
const PORT = 3000;
const FILE = 'data/users.json';

// Load saved users (or start empty). Shape: [{ user: "fred", password: "1234" }, ...]
fs.mkdirSync('data', { recursive: true });
const users = fs.existsSync(FILE) ? JSON.parse(fs.readFileSync(FILE, 'utf8')) : [];

app.use(express.json());

// Keep the saved data private (static files are served from '.')
app.use('/data', (req, res) => res.sendStatus(404));

// Sign up: save what was typed in
app.post('/api/admin/signup', (req, res) => {
    const { user, password } = req.body;
    if (!user || !password) return res.status(400).json({ error: 'Enter a username and password.' });
    if (users.some(u => u.user === user)) return res.status(409).json({ error: 'Username already taken.' });

    users.push({ user, password });
    fs.writeFileSync(FILE, JSON.stringify(users, null, 2));
    res.status(201).json({ user });
});

// Log in: check what was typed in against what was saved
app.post('/api/admin/login', (req, res) => {
    const { user, password } = req.body;
    const found = users.find(u => u.user === user && u.password === password);
    if (!found) return res.status(401).json({ error: 'Incorrect username or password.' });
    res.json({ user });
});

// Serve all static files (index.html, CSS, JS, images) from the current folder
app.use(express.static('.'));

app.listen(PORT, () => {
    console.log(`Server running at http://localhost:${PORT}/`);
});