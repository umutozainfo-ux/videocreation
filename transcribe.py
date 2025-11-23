# transcribe.py
from faster_whisper import WhisperModel
import os

class Transcriber:
    def __init__(self, model_size="large-v3"):
        self.model = WhisperModel(
            model_size,
            device="cuda" if os.path.exists("/usr/local/cuda") else "cpu",
            compute_type="float16" if os.path.exists("/usr/local/cuda") else "int8"
        )

    def transcribe_file(self, audio_path, language=None, word_timestamps=True):
        segments, info = self.model.transcribe(
            audio_path,
            language=language,
            beam_size=5,
            best_of=5,
            patience=1.0,
            word_timestamps=word_timestamps
        )
        
        result = []
        for segment in segments:
            for word in segment.words:
                result.append({
                    "text": word.word.strip(),
                    "start": word.start,
                    "end": word.end,
                    "probability": word.probability
                })
        return {
            "words": result,
            "language": info.language,
            "duration": info.duration
        }

transcriber = Transcriber("large-v3")  # or "medium", "small" for faster