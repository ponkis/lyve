import urllib.request
import urllib.parse
import json
import librosa

def fetch_bpm_from_deezer(artist, title):
    """
    Fetches the BPM of a track from Deezer's public API.
    """
    if not artist or not title:
        return None
        
    user_agent = "Lyve/1.0 (https://github.com/ponkis/lyve)"
    
    # Try strict first, then loose
    queries = [
        f'artist:"{artist}" track:"{title}"',
        f"{artist} {title}"
    ]
    
    for query in queries:
        try:
            url = f"https://api.deezer.com/search?q={urllib.parse.quote(query)}"
            req = urllib.request.Request(url, headers={"User-Agent": user_agent})
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode('utf-8'))
                    if data and "data" in data and len(data["data"]) > 0:
                        # Retrieve the track detail endpoint using the track ID of the first match
                        track_id = data["data"][0]["id"]
                        track_url = f"https://api.deezer.com/track/{track_id}"
                        
                        track_req = urllib.request.Request(track_url, headers={"User-Agent": user_agent})
                        with urllib.request.urlopen(track_req, timeout=10) as track_resp:
                            if track_resp.status == 200:
                                track_data = json.loads(track_resp.read().decode('utf-8'))
                                bpm_val = track_data.get("bpm")
                                if bpm_val:
                                    bpm_float = float(bpm_val)
                                    if bpm_float > 0:
                                        return bpm_float
        except Exception as e:
            print(f"Error fetching BPM from Deezer with query '{query}': {e}")
            
    return None


def detect_bpm(file_path):
    try:
        y, sr = librosa.load(file_path, duration=60)
        onset_env = librosa.onset.onset_strength(y=y, sr=sr)
        tempo = librosa.feature.tempo(onset_envelope=onset_env, sr=sr)
        
        if tempo is not None and len(tempo) > 0:
            return float(tempo[0])
            
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        return float(tempo)
        
    except AttributeError as e:
        print(f"AttributeError in BPM detection (scipy issue): {e}")
        try:
            y, sr = librosa.load(file_path, duration=30)
            tempo = librosa.feature.tempo(y=y, sr=sr)
            if tempo is not None and len(tempo) > 0:
                return float(tempo[0])
        except Exception:
            pass
        return 120.0
    except Exception as e:
        print(f"Error detecting BPM: {e}")
        return 120.0
