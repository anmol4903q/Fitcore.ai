from flask import Flask, request, jsonify
from flask_cors import CORS
from groq import Groq

app = Flask(__name__)
CORS(app)
client = Groq(api_key="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")

@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    message = data.get('message')
    history = data.get('history', [])

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": "You are FITCORE.AI, a fitness and mental wellness assistant. Give workout plans, diet advice, and mental health tips. Be concise and motivating."},
            *history,
            {"role": "user", "content": message}
        ]
    )
    return jsonify({"reply": response.choices[0].message.content})

if __name__ == '__main__':
    app.run(debug=True, port=5000)