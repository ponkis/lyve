import urllib.request
import urllib.parse
import json
import re

_whisper_model = None

def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        from lyve.config import Config
        print("Initializing Whisper model...")
        _whisper_model = WhisperModel(
            Config.WHISPER_MODEL_SIZE, 
            device=Config.WHISPER_DEVICE, 
            compute_type=Config.WHISPER_COMPUTE_TYPE
        )
    return _whisper_model


def fetch_lyrics_from_lrclib(artist, title, album, duration):
    """
    Fetches lyrics from lrclib.net.
    1. Tries /api/get (strict)
    2. Tries /api/search?q=... (fuzzy) and finds the closest duration match
    Returns dict with {"syncedLyrics": ..., "plainLyrics": ..., "instrumental": ...} or None.
    """
    if not artist or not title:
        return None
        
    user_agent = "Lyve/1.0 (https://github.com/ponkis/lyve)"
    
    # 1. Try strict GET /api/get
    try:
        params = {
            "artist_name": artist,
            "track_name": title,
            "album_name": album or "",
            "duration": int(duration) if duration else 0
        }
        query_string = urllib.parse.urlencode(params)
        url = f"https://lrclib.net/api/get?{query_string}"
        
        req = urllib.request.Request(url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode('utf-8'))
                if data:
                    return data
    except urllib.error.HTTPError as e:
        # 404 is normal if not found, let it proceed to search
        if e.code != 404:
            print(f"LRCLIB strict lookup HTTP error: {e.code}")
    except Exception as e:
        print(f"LRCLIB strict lookup error: {e}")

    # 2. Try search GET /api/search?q=...
    try:
        query = f"{artist} {title}"
        url = f"https://lrclib.net/api/search?q={urllib.parse.quote(query)}"
        req = urllib.request.Request(url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status == 200:
                results = json.loads(resp.read().decode('utf-8'))
                if results and isinstance(results, list):
                    best_match = None
                    best_diff = 999999.0
                    
                    for res in results:
                        # Skip if there's no lyrics at all
                        if not res.get("syncedLyrics") and not res.get("plainLyrics"):
                            continue
                        
                        res_dur = res.get("duration")
                        if res_dur is not None and duration:
                            diff = abs(float(res_dur) - float(duration))
                        else:
                            diff = 999999.0
                            
                        # Accept if duration is within 15 seconds, or if we have no duration
                        if not duration or diff <= 15.0:
                            # Prefer synced lyrics, then select the one with smaller duration diff
                            if best_match is None:
                                best_match = res
                                best_diff = diff
                            else:
                                # Prioritize synced lyrics over unsynced
                                current_has_sync = bool(res.get("syncedLyrics"))
                                best_has_sync = bool(best_match.get("syncedLyrics"))
                                
                                if current_has_sync and not best_has_sync:
                                    best_match = res
                                    best_diff = diff
                                elif current_has_sync == best_has_sync:
                                    if diff < best_diff:
                                        best_match = res
                                        best_diff = diff
                                        
                    if best_match:
                        return best_match
    except Exception as e:
        print(f"LRCLIB search fallback error: {e}")
        
    return None


def parse_lrc(lrc_text, duration):
    """
    Parses LRC format lyrics and interpolates line-level timestamps into word-level timestamps.
    Returns a list of dicts: [{"word": word, "start": start, "end": end}, ...]
    """
    lines = []
    if not lrc_text:
        return lines

    pattern = re.compile(r'\[(\d+):(\d+(?:\.\d+)?)]')
    
    for line in lrc_text.splitlines():
        line = line.strip()
        if not line:
            continue
            
        # Find all timestamps in this line
        matches = list(pattern.finditer(line))
        if not matches:
            continue
            
        # The text is after the last timestamp
        last_match = matches[-1]
        text = line[last_match.end():].strip()
        
        for match in matches:
            minutes = int(match.group(1))
            seconds = float(match.group(2))
            time_in_seconds = minutes * 60 + seconds
            lines.append({"time": time_in_seconds, "text": text})
            
    # Sort lines by start time
    lines.sort(key=lambda x: x["time"])
    
    # Now interpolate words
    words = []
    for i, line_data in enumerate(lines):
        line_start = line_data["time"]
        line_text = line_data["text"]
        words_in_line = line_text.split()
        if not words_in_line:
            continue
            
        # Determine when the line ends
        if i < len(lines) - 1:
            line_end = lines[i+1]["time"]
        else:
            line_end = duration if duration else (line_start + 5.0)
            
        line_duration = line_end - line_start
        if line_duration <= 0:
            line_duration = 2.0
            
        # Word timing heuristic: average words take 0.3 - 0.4 seconds, up to a max
        total_words = len(words_in_line)
        word_duration = max(0.15, min(0.4, line_duration / total_words))
        
        for j, w in enumerate(words_in_line):
            start = line_start + j * word_duration
            end = line_start + (j + 1) * word_duration
            words.append({"word": w, "start": start, "end": end})
            
    return words


def transcribe_local(filepath, duration):
    """
    Transcribes the local audio file using faster-whisper.
    """
    try:
        model = get_whisper_model()
        segments, info = model.transcribe(filepath, word_timestamps=True)
        total_duration = (
            info.duration if hasattr(info, "duration") and info.duration else None
        )
        if total_duration is None:
            total_duration = duration

        full_lyrics = ""
        timed_words = []
        last_end = 0.0
        
        # We process segments generator
        segments_list = list(segments)
        for i, segment in enumerate(segments_list):
            full_lyrics += segment.text + " "
            for word in getattr(segment, "words", []):
                timed_words.append(
                    {"word": word.word, "start": word.start, "end": word.end}
                )
                last_end = max(last_end, word.end)
                
        return timed_words, full_lyrics.strip()
    except Exception as e:
        print(f"Error with local Whisper transcription: {e}")
        return None, None
