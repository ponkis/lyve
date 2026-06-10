import os
import mutagen

def parse_filename_fallback(original_filename):
    """
    Extracts artist and title from filename by splitting on common separators.
    """
    if not original_filename:
        return "", ""
    base, _ = os.path.splitext(original_filename)
    # Common separators: " - ", " -", "- ", "-"
    for sep in [" - ", " -", "- ", "-"]:
        if sep in base:
            parts = base.split(sep, 1)
            artist = parts[0].strip()
            title = parts[1].strip()
            if artist and title:
                return artist, title
    return "", base.strip()


def extract_metadata_and_bpm(file_path, original_filename):
    """
    Extracts artist, title, album, and bpm tags from the file using mutagen.
    Falls back to filename parsing if artist/title are empty.
    """
    artist, title, album, bpm = "", "", "", None
    
    # Try reading tags via mutagen.File easy=True
    try:
        audio = mutagen.File(file_path, easy=True)
        if audio is not None:
            artist = audio.get("artist", [""])[0].strip()
            title = audio.get("title", [""])[0].strip()
            album = audio.get("album", [""])[0].strip()
            bpm_val = audio.get("bpm")
            if bpm_val:
                try:
                    bpm = float(bpm_val[0])
                except (ValueError, TypeError):
                    pass
            # Some easy tags map tempo to "tempo"
            if bpm is None:
                tempo_val = audio.get("tempo")
                if tempo_val:
                    try:
                        bpm = float(tempo_val[0])
                    except (ValueError, TypeError):
                        pass
    except Exception as e:
        print(f"Error reading easy mutagen tags: {e}")

    # Fallback to raw tags if EasyID3/EasyTag didn't capture bpm
    if bpm is None:
        try:
            audio_raw = mutagen.File(file_path)
            if audio_raw is not None:
                # MP3 (ID3 tags)
                if hasattr(audio_raw, "tags") and audio_raw.tags:
                    for tag in ["TBPM", "bpm", "tempo"]:
                        if tag in audio_raw.tags:
                            try:
                                val = audio_raw.tags[tag]
                                if hasattr(val, "text"):
                                    bpm = float(val.text[0])
                                else:
                                    bpm = float(val[0])
                                break
                            except (ValueError, TypeError, IndexError):
                                pass
                # MP4/M4A
                if bpm is None and hasattr(audio_raw, "get"):
                    tmpo = audio_raw.get("tmpo")
                    if tmpo:
                        try:
                            bpm = float(tmpo[0])
                        except (ValueError, TypeError):
                            pass
        except Exception as e:
            print(f"Error reading raw mutagen tags: {e}")

    # Fallback to filename if artist or title is missing
    if not artist or not title:
        f_artist, f_title = parse_filename_fallback(original_filename)
        if not artist and f_artist:
            artist = f_artist
        if not title and f_title:
            title = f_title

    # Filter out empty bpm values (e.g. 0.0)
    if bpm is not None and bpm <= 0:
        bpm = None

    return artist, title, album, bpm
