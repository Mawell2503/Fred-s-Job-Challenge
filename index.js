import express from 'express';

const app = express();
const PORT = 3000;

// Serve all static files (index.html, CSS, JS, images) from the current folder
app.use(express.static('.'));

app.listen(PORT, () => {
    console.log(`Server running at http://localhost:${PORT}/`);
});