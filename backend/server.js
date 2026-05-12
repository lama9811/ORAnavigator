const express = require('express');
const cors = require('cors');
const app = express();
const port = 5000;

app.use(express.json());
app.use(cors()); // Enable CORS for frontend access

app.post('/api/chat', (req, res) => {
  const { message } = req.body;
  const response = `Bot: You said: ${message}`;
  res.json({ reply: response });
});

app.listen(port, () => {
  console.log(`Backend server running at http://localhost:${port}`);
});