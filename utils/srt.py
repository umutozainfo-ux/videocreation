# utils/srt.py

def words_to_srt(words):
    """
    Convert word-by-word transcription to SRT format
    Each word becomes its own SRT entry
    """
    srt_content = ""
    
    for i, word in enumerate(words, 1):
        # Convert seconds to SRT timestamp format (HH:MM:SS,mmm)
        start_time = _format_timestamp(word['start'])
        end_time = _format_timestamp(word['end'])
        
        srt_content += f"{i}\n"
        srt_content += f"{start_time} --> {end_time}\n"
        srt_content += f"{word['text']}\n\n"
    
    return srt_content

def words_to_ass(words):
    """
    Convert word-by-word transcription to ASS format
    Each word becomes its own ASS dialogue
    """
    ass_content = """[Script Info]
ScriptType: v4.00+
PlayResX: 384
PlayResY: 288
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    
    for i, word in enumerate(words):
        start_time = _format_timestamp_ass(word['start'])
        end_time = _format_timestamp_ass(word['end'])
        
        ass_content += f"Dialogue: 0,{start_time},{end_time},Default,,0,0,0,,{word['text']}\n"
    
    return ass_content

def words_to_ass_advanced(words):
    """
    Advanced ASS format with word-level styling
    """
    return words_to_ass(words)

def _format_timestamp(seconds):
    """Convert seconds to SRT timestamp format (HH:MM:SS,mmm)"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    milliseconds = int((secs - int(secs)) * 1000)
    
    return f"{hours:02d}:{minutes:02d}:{int(secs):02d},{milliseconds:03d}"

def _format_timestamp_ass(seconds):
    """Convert seconds to ASS timestamp format (H:MM:SS.cc)"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    centiseconds = int((secs - int(secs)) * 100)
    
    return f"{hours}:{minutes:02d}:{int(secs):02d}.{centiseconds:02d}"