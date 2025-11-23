# utils/srt.py
def words_to_srt(words):
    lines = []
    for i, word in enumerate(words, 1):
        start = format_timestamp(word['start'])
        end = format_timestamp(word['end'])
        lines.append(f"{i}\n{start} --> {end}\n{word['text']}\n")
    return "\n".join(lines)

def format_timestamp(seconds):
    from datetime import timedelta
    td = timedelta(seconds=seconds)
    hours, remainder = divmod(td.total_seconds(), 3600)
    minutes, seconds = divmod(remainder, 60)
    milliseconds = int((seconds - int(seconds)) * 1000)
    seconds = int(seconds)
    return f"{int(hours):02}:{int(minutes):02}:{seconds:02},{milliseconds:03}"