# app.py
from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.utils import secure_filename
from transcribe import transcriber
from utils.srt import words_to_srt
import os
from datetime import datetime
import ffmpeg

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB max

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def extract_audio(file_path):
    audio_path = file_path + ".wav"
    try:
        stream = ffmpeg.input(file_path)
        stream = ffmpeg.output(stream, audio_path, acodec="pcm_s16le", ar="16k", ac=1)
        ffmpeg.run(stream, overwrite_output=True, quiet=True)
        return audio_path
    except Exception as e:
        print("FFmpeg error:", e)
        return file_path

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/live')
def live():
    return render_template('live.html')

@app.route('/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        return jsonify({"error": "No file"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    # Extract audio
    audio_path = extract_audio(filepath)

    # Transcribe
    try:
        result = transcriber.transcribe_file(audio_path, word_timestamps=True)
        
        # Generate SRT
        srt_content = words_to_srt(result['words'])
        srt_filename = filename.rsplit('.', 1)[0] + ".srt"
        srt_path = os.path.join(app.config['UPLOAD_FOLDER'], srt_filename)
        with open(srt_path, 'w', encoding='utf-8') as f:
            f.write(srt_content)

        return jsonify({
            "success": True,
            "words": result['words'],
            "srt_url": f"/download/{srt_filename}",
            "language": result['language']
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/download/<filename>')
def download(filename):
    return send_file(os.path.join(app.config['UPLOAD_FOLDER'], filename), as_attachment=True)

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)