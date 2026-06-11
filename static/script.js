document.addEventListener('DOMContentLoaded', () => {
   const footer = document.querySelector('footer');
   const mainContainer = document.getElementById('main-container');
   const visualizationMode = document.getElementById('visualization-mode');
   const startBtn = document.getElementById('start-btn');
   const resetBtn = document.getElementById('reset-btn');
   const audioPlayer = document.getElementById('audio-player');

   function hideUIElements() {
      if (footer) footer.classList.add('hidden');
   }

   function showUIElements() {
      if (footer) footer.classList.remove('hidden');
   }

   if (startBtn) {
      startBtn.addEventListener('click', () => {
         hideUIElements();
      });
   }

   if (resetBtn) {
      resetBtn.addEventListener('click', () => {
         showUIElements();
      });
   }
   if (audioPlayer) {
      audioPlayer.addEventListener('ended', () => {
         showUIElements();
      });
   }
   document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && visualizationMode && visualizationMode.classList.contains('active')) {
         showUIElements();
      }
   });

   window.showUIElements = showUIElements;
});

let audioData = null;
let isPlaying = false;
let wordIndex = 0;
let animationInterval = null;
let nextWordTimeout = null;

const fileInput = document.getElementById('file-input');
const uploadBtn = document.getElementById('upload-btn');
const uploadSection = document.getElementById('upload-section');
const playerSection = document.getElementById('player-section');
const audioPlayer = document.getElementById('audio-player');
const startBtn = document.getElementById('start-btn');
const resetBtn = document.getElementById('reset-btn');
const bpmInfo = document.getElementById('bpm-info');
const lyricsSource = document.getElementById('lyrics-source');
const loading = document.getElementById('loading');
const canvas = document.getElementById('lyrics-canvas');
const ctx = canvas.getContext('2d');
const lyricsTextarea = document.getElementById('lyrics-textarea');
const bpmDoubleCheckbox = document.getElementById('bpm-double');
const mainContainer = document.getElementById('main-container');
const visualizationMode = document.getElementById('visualization-mode');
const flashOverlay = document.getElementById('flash-overlay');
const lyricsEditTextarea = document.getElementById('lyrics-edit-textarea');
const reprocessBtn = document.getElementById('reprocess-btn');
const lyricsEditorAi = document.getElementById('lyrics-editor-ai');
const lyricsEditSection = document.getElementById('lyrics-edit-section');
const timedWordsContainer = document.getElementById('timed-words-container');
const loadPonkBtn = document.getElementById('load-ponk-btn');
const ponkFileInput = document.getElementById('ponk-file-input');
const downloadPonkBtn = document.getElementById('download-ponk-btn');
const lyricsWorld = document.getElementById('lyrics-world');
const footer = document.querySelector('footer');

// Progress Bar & .ponk Upload Modal Selectors
const ponkModal = document.getElementById('ponk-modal');
const ponkModalFilename = document.getElementById('ponk-modal-filename');
const ponkModalSelectBtn = document.getElementById('ponk-modal-select-btn');
const progressBarFill = document.getElementById('progress-bar-fill');
const progressCurrentTime = document.getElementById('progress-current-time');
const progressTotalTime = document.getElementById('progress-total-time');

let flashInterval = null;
let flashTimeout = null;
let ponkFileContent = null;

const allowedAudioExtensions = new Set(['mp3', 'wav', 'flac', 'ogg', 'm4a']);
const allowedAudioTypes = new Set([
   'audio/aac',
   'audio/flac',
   'audio/m4a',
   'audio/mp4',
   'audio/mpeg',
   'audio/mp3',
   'audio/ogg',
   'audio/wav',
   'audio/wave',
   'audio/x-flac',
   'audio/x-m4a',
   'audio/x-mpeg',
   'audio/x-wav',
   'application/ogg',
   'video/mp4'
]);

let currentScale = 1.0;
let targetScale = 1.0;
let currentX = 0,
   currentY = 0;
let targetX = 0,
   targetY = 0;
let isManualZooming = false;
let isDragging = false;
let startX, startY;
let initialTranslateX = 0,
   initialTranslateY = 0;
let autoZoomInterval = null;
let animationFrameId = null;
let lastWordPosition = {
   x: 0,
   y: 0
};

const secretCode = "chromakopia";
let keySequence = "";

canvas.width = window.innerWidth;
canvas.height = window.innerHeight;

window.addEventListener('resize', () => {
   canvas.width = window.innerWidth;
   canvas.height = window.innerHeight;
});

uploadBtn.addEventListener('click', () => {
   fileInput.click();
});

fileInput.addEventListener('change', async (e) => {
   const files = Array.from(e.target.files);
   const file = e.target.files[0];
   if (!file) return;

   const validationMessage = validateSelectedAudioFile(file);
   if (validationMessage) {
      alert(validationMessage);
      fileInput.value = '';
      return;
   }

   try {
      const tempAudio = new Audio(URL.createObjectURL(file));
      await new Promise((resolve, reject) => {
         const onMeta = () => {
            resolve();
            cleanup();
         };
         const onErr = () => {
            reject(new Error('Failed to load audio metadata'));
            cleanup();
         };

         function cleanup() {
            tempAudio.removeEventListener('loadedmetadata', onMeta);
            tempAudio.removeEventListener('error', onErr);
         }
         tempAudio.addEventListener('loadedmetadata', onMeta);
         tempAudio.addEventListener('error', onErr);
      });
      const maxDuration = Number(fileInput.dataset.maxDuration || 270);
      if (tempAudio.duration > maxDuration) {
         alert(`The file ${file.name} exceeds the maximum duration of ${formatTime(maxDuration)} minutes.`);

         fileInput.value = '';
         return;
      }
   } catch (err) {

      console.warn('Could not verify file duration prior to upload:', err);
   }

   loading.style.display = 'block';

   uploadBtn.disabled = true;

   const formData = new FormData();
   formData.append('file', file);

   if (ponkFileContent) {
      formData.append('is_ponk_validation', 'true');
   } else {

      formData.append('lyrics_source', 'ai');
   }

   try {
      const response = await fetch('/upload', {
         method: 'POST',
         body: formData
      });

      const contentType = response.headers.get('content-type') || '';
      let data;
      if (!response.ok) {

         const text = await response.text();
         throw new Error(`Server returned ${response.status} ${response.statusText}: ${text.substring(0,500)}`);
      }

      if (contentType.includes('application/json')) {
         data = await response.json();
      } else {
         const text = await response.text();
         throw new Error(`Expected JSON but received ${contentType || 'text'}: ${text.substring(0,500)}`);
      }

      if (data.success) {

         if (response.status === 202 && data.file_hash) {
            const fileHash = data.file_hash;

            let statusSpan = loading.querySelector('.status-text');
            if (!statusSpan) {
               statusSpan = document.createElement('span');
               statusSpan.className = 'status-text';
               statusSpan.style.display = 'block';
               statusSpan.style.marginTop = '10px';
               loading.appendChild(statusSpan);
            }
            statusSpan.textContent = 'Processing uploaded file (0%)...';

            if (data.queue_position) {
               statusSpan.textContent = `Queued ${data.queue_position} - please wait...`;
            }

            let attempts = 0;
            const poll = async () => {
               attempts++;
               try {
                  const st = await fetch(`/status/${fileHash}`);
                  if (st.ok) {
                     const stj = await st.json();
                     const prog = stj.progress != null ? stj.progress : 0;
                     const statusText = stj.status || 'processing';

                     if (statusText === 'queued') {
                        const pos = stj.queue_position != null ? stj.queue_position : '';
                        statusSpan.textContent = pos ? `Queued ${pos} - please wait...` : 'Queued - please wait...';
                     } else {
                        statusSpan.textContent = `Processing uploaded file (${prog}%)...`;
                     }

                     if (stj.status === 'ready' || prog >= 100) {
                        const res = await fetch(`/result/${fileHash}`);
                        if (res.ok) {
                           data = await res.json();
                           return;
                        }
                     }
                  }
                  if (attempts < 600) {
                     await new Promise(r => setTimeout(r, 2000));
                     return poll();
                  }
                  throw new Error('Processing timed out on server');
               } catch (err) {
                  throw err;
               }
            };
            await poll();
         }

         if (ponkFileContent) {
            const ponkMeta = ponkFileContent.metadata;
            const audioMeta = data.metadata;

            if (ponkMeta.fileSize !== audioMeta.fileSize || Math.abs(ponkMeta.duration - audioMeta.duration) > 0.1) {
               alert('Audio file does not match the loaded .ponk file. Please select the correct audio file.');
               ponkFileContent = null;
               loading.style.display = 'none';
               uploadBtn.disabled = false;
               fileInput.value = '';
               return;
            }

            data.lyrics = ponkFileContent.lyrics;
            data.words = ponkFileContent.words;
            data.lyrics_source = ponkFileContent.lyrics_source;

            audioData = data;
            audioPlayer.src = `/uploads/${data.filename || (data.metadata && data.metadata.originalFilename)}`;

            let sourceText = 'From .ponk file';
            lyricsSource.textContent = `Lyrics: ${sourceText}`;
            bpmInfo.textContent = `BPM: ${Math.round(data.bpm)}`;

            if (data.lyrics_source && data.lyrics_source.startsWith('lrclib')) {
               lyricsEditSection.style.display = 'none';
               downloadPonkBtn.style.display = 'none';
            } else {
               lyricsEditSection.style.display = 'block';
               lyricsEditorAi.style.display = 'block';
               populateTimedWordsEditor(data.words);
               downloadPonkBtn.style.display = 'block';
            }

            uploadSection.style.display = 'none';
            playerSection.style.display = 'block';

            const statusSpan = loading.querySelector('.status-text');
            if (statusSpan) statusSpan.textContent = 'Processing complete.';

            setTimeout(() => {
               loading.style.display = 'none';
            }, 800);
            uploadBtn.disabled = false;
            ponkFileContent = null;
            return;
         }

         audioData = data;

         audioPlayer.src = `/uploads/${data.filename || (data.metadata && data.metadata.originalFilename)}`;

         bpmInfo.textContent = `BPM: ${Math.round(data.bpm)}`;

         let sourceText = 'From file metadata';
         if (data.lyrics_source === 'manual') {
            sourceText = 'Manual input';
         } else if (data.lyrics_source === 'gibberish') {
            sourceText = 'Generated (No metadata found)';
         } else if (data.lyrics_source === 'ai') {
            sourceText = 'AI Transcription (Whisper)';
         } else if (data.lyrics_source === 'lrclib') {
            sourceText = 'Synced Lyrics (LRCLIB)';
         } else if (data.lyrics_source === 'lrclib_unsynced') {
            sourceText = 'Unsynced Lyrics (LRCLIB)';
         }
         lyricsSource.textContent = `Lyrics: ${sourceText}`;

         if (data.lyrics_source && data.lyrics_source.startsWith('lrclib')) {
            lyricsEditSection.style.display = 'none';
            downloadPonkBtn.style.display = 'none';
         } else {
            lyricsEditSection.style.display = 'block';
            lyricsEditorAi.style.display = 'block';
            populateTimedWordsEditor(data.words);
            downloadPonkBtn.style.display = 'block';
         }

         uploadSection.style.display = 'none';
         playerSection.style.display = 'block';

         wordIndex = 0;
      } else {
         alert('Error: ' + data.error);
      }
   } catch (error) {
      alert('Error uploading file: ' + error.message);
   } finally {
      loading.style.display = 'none';
      uploadBtn.disabled = false;
   }
});

reprocessBtn.addEventListener('click', () => {
   if (!audioData) return;

   const inputs = timedWordsContainer.querySelectorAll('.word-input');
   let fullLyrics = [];
   inputs.forEach(input => {
      const index = parseInt(input.dataset.index, 10);

      if (audioData.words[index]) {
         audioData.words[index].word = input.value;
      }
      fullLyrics.push(input.value);
   });
   audioData.lyrics = fullLyrics.join(' ');

   lyricsSource.textContent = 'Lyrics: Manual input (Updated)';
   wordIndex = 0;

   alert('Lyrics updated!');
});

startBtn.addEventListener('click', () => {
   if (!audioData) return;
   lastWordPosition = {
      x: window.innerWidth / 2,
      y: window.innerHeight / 2
   };

   mainContainer.classList.add('hidden');
   visualizationMode.classList.add('active');

   document.body.classList.add('no-scroll');

   audioPlayer.play();
   startVisualization();
   startBtn.disabled = true;

   startAnimationLoop();

   autoZoomInterval = setInterval(triggerAutoZoom, 4 * (60 / (audioData.bpm || 120)) * 1000);
});

document.addEventListener('keydown', (e) => {
   if (e.key === 'Escape' && visualizationMode.classList.contains('active')) {
      exitVisualization();
   }
});

document.addEventListener('keydown', (e) => {
   if (e.key.length === 1 && e.key.match(/[a-z]/i)) {
      keySequence += e.key.toLowerCase();
      if (keySequence.length > secretCode.length) {
         keySequence = keySequence.slice(1);
      }
      if (keySequence === secretCode) {
         visualizationMode.classList.toggle('chromakopia-mode');
         keySequence = "";
      }
   }
});

function exitVisualization() {
   stopVisualization();

   if (progressBarFill) progressBarFill.style.width = '0%';
   if (progressCurrentTime) progressCurrentTime.textContent = '0:00';
   if (progressTotalTime) progressTotalTime.textContent = '0:00';

   visualizationMode.classList.remove('active');
   visualizationMode.removeEventListener('wheel', handleZoom);
   visualizationMode.removeEventListener('mousedown', handleDragStart);
   visualizationMode.removeEventListener('mousemove', handleDragMove);
   visualizationMode.removeEventListener('mouseup', handleDragEnd);
   visualizationMode.removeEventListener('mouseleave', handleDragEnd);
   mainContainer.classList.remove('hidden');
   audioPlayer.pause();
   startBtn.disabled = false;
   wordIndex = 0;

   stopAnimationLoop();
   if (autoZoomInterval) clearInterval(autoZoomInterval);
   currentScale = 1.0;
   targetScale = 1.0;
   currentX = 0;
   targetX = 0;
   currentY = 0;
   targetY = 0;
   manualTranslateX = 0;
   manualTranslateY = 0;
   isManualZooming = false;
   isDragging = false;
   lastWordPosition = {
      x: window.innerWidth / 2,
      y: window.innerHeight / 2
   };

   if (flashInterval) {
      clearInterval(flashInterval);
      flashInterval = null;
   }

   document.querySelectorAll('.word-element').forEach(el => el.remove());
   lyricsWorld.innerHTML = '';
   lyricsWorld.style.transform = 'translate(0, 0)';

   if (window.showUIElements) window.showUIElements();

   document.body.classList.remove('no-scroll');
}

resetBtn.addEventListener('click', () => {
   exitVisualization();
   audioPlayer.currentTime = 0;
   audioData = null;
   wordIndex = 0;

   playerSection.style.display = 'none';
   uploadSection.style.display = 'block';
   startBtn.disabled = false;

   if (fileInput) fileInput.value = '';
   if (lyricsTextarea) lyricsTextarea.value = '';
   if (bpmDoubleCheckbox) bpmDoubleCheckbox.checked = false;

   if (loadPonkBtn) loadPonkBtn.classList.remove('loaded');
});

bpmDoubleCheckbox.addEventListener('change', () => {
   if (!audioData) return;
   const displayBpm = bpmDoubleCheckbox.checked ? audioData.bpm * 2 : audioData.bpm;
   bpmInfo.textContent = `BPM: ${Math.round(displayBpm)}`;
});

audioPlayer.addEventListener('ended', () => {
   exitVisualization();
   startBtn.disabled = false;
   wordIndex = 0;
});

audioPlayer.addEventListener('pause', () => {
   if (animationInterval && visualizationMode.classList.contains('active')) {
      exitVisualization();
      startBtn.disabled = false;
   }
});

function formatTime(seconds) {
   if (isNaN(seconds) || seconds === null) return '0:00';
   const mins = Math.floor(seconds / 60);
   const secs = Math.floor(seconds % 60);
   return `${mins}:${secs.toString().padStart(2, '0')}`;
}

function formatBytes(bytes) {
   if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
   const units = ['B', 'KB', 'MB', 'GB'];
   let size = bytes;
   let unitIndex = 0;
   while (size >= 1024 && unitIndex < units.length - 1) {
      size /= 1024;
      unitIndex++;
   }
   return `${size.toFixed(size >= 10 || unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

function validateSelectedAudioFile(file) {
   const extension = (file.name.split('.').pop() || '').toLowerCase();
   if (!allowedAudioExtensions.has(extension)) {
      return 'Please choose an MP3, WAV, FLAC, OGG, or M4A audio file.';
   }

   if (file.type && !allowedAudioTypes.has(file.type.toLowerCase())) {
      return 'This file does not report an allowed audio content type.';
   }

   const maxSize = Number(fileInput.dataset.maxSize || 0);
   if (maxSize && file.size > maxSize) {
      return `The file ${file.name} is too large. Maximum size is ${formatBytes(maxSize)}.`;
   }

   if (file.size <= 0) {
      return 'The selected file is empty.';
   }

   return '';
}

audioPlayer.addEventListener('timeupdate', () => {
   if (!audioPlayer || isNaN(audioPlayer.duration)) return;
   const current = audioPlayer.currentTime;
   const duration = audioPlayer.duration;
   if (progressCurrentTime) {
      progressCurrentTime.textContent = formatTime(current);
   }
   if (progressTotalTime) {
      progressTotalTime.textContent = formatTime(duration);
   }
   if (progressBarFill) {
      const percentage = (current / duration) * 100;
      progressBarFill.style.width = `${percentage}%`;
   }
});

audioPlayer.addEventListener('durationchange', () => {
   if (audioPlayer && !isNaN(audioPlayer.duration) && progressTotalTime) {
      progressTotalTime.textContent = formatTime(audioPlayer.duration);
   }
});

audioPlayer.addEventListener('seeked', () => {
   if (!isPlaying || !audioData) return;

   const currentTime = audioPlayer.currentTime;

   if (audioData.words && audioData.words.length > 0 && audioData.words[0] && audioData.words[0].hasOwnProperty('start')) {

      wordIndex = audioData.words.findIndex(word => word.start >= currentTime);
      if (wordIndex === -1) wordIndex = audioData.words.length;

      stopVisualization();
      startVisualization();
   }

});

function startVisualization() {
   if (!audioData || !audioData.words) return;

   isPlaying = true;

   if (audioData.words && audioData.words.length > 0 && audioData.words[0] && audioData.words[0].hasOwnProperty('start')) {
      function scheduleNextWord() {
         if (wordIndex >= audioData.words.length || !isPlaying) {
            return;
         }

         const currentWord = audioData.words[wordIndex];
         const currentTime = audioPlayer.currentTime;
         const delay = (currentWord.start - currentTime) * 1000;

         if (delay < 0) {

            while (wordIndex < audioData.words.length && audioData.words[wordIndex].start < currentTime) {
               wordIndex++;
            }
            scheduleNextWord();
            return;
         }

         nextWordTimeout = setTimeout(() => {
            const nextWord = audioData.words[wordIndex + 1];
            displayWord(currentWord.word, currentWord, nextWord);
            wordIndex++;
            scheduleNextWord();
         }, delay);
      }

      if (animationInterval) clearInterval(animationInterval);

      scheduleNextWord();

   } else {

      let effectiveBpm = audioData.bpm;
      if (bpmDoubleCheckbox.checked) {
         effectiveBpm = audioData.bpm * 2;
      }
      const beatInterval = (60 / effectiveBpm) * 1000;

      animationInterval = setInterval(() => {
         if (wordIndex >= audioData.words.length) {
            wordIndex = 0;
         }
         displayWord(audioData.words[wordIndex]);
         wordIndex++;
      }, beatInterval);
   }

   let effectiveBpm = audioData.bpm;
   if (bpmDoubleCheckbox.checked) {
      effectiveBpm = audioData.bpm * 2;
   }
   const doubleBeatInterval = (60 / effectiveBpm) * 1000 * 2;

   if (flashInterval) clearInterval(flashInterval);
   flashInterval = setInterval(() => {
      triggerFlash();
   }, doubleBeatInterval);
}

function triggerFlash() {

   flashOverlay.classList.remove('flash');

   void flashOverlay.offsetWidth;

   flashOverlay.classList.add('flash');

   if (flashTimeout) {
      clearTimeout(flashTimeout);
   }
   flashTimeout = setTimeout(() => {
      flashOverlay.classList.remove('flash');
   }, 150);
}

function stopVisualization() {
   isPlaying = false;
   if (animationInterval) {
      clearInterval(animationInterval);
      animationInterval = null;
   }
   if (flashInterval) {
      clearInterval(flashInterval);
      flashInterval = null;
   }
   if (nextWordTimeout) {
      clearTimeout(nextWordTimeout);
      nextWordTimeout = null;
   }
}

function displayWord(word, wordData, nextWordData) {

   const wordElement = document.createElement('div');
   wordElement.className = 'word-element';
   wordElement.textContent = word;

   const fontSize = Math.random() * 60 + 30;
   const rotation = (Math.random() - 0.5) * 40;

   const isChromakopia = visualizationMode.classList.contains('chromakopia-mode');

   const colors = isChromakopia ? ['#000000'] : [
      '#FF6B6B', '#4ECDC4', '#45B7D1', '#FFA07A', '#98D8C8',
      '#F7DC6F', '#BB8FCE', '#85C1E2', '#F8B739', '#52B788',
      '#E76F51', '#F4A261', '#E9C46A', '#2A9D8F', '#264653',
      '#E63946', '#F1FAEE', '#A8DADC', '#457B9D', '#1D3557',
      '#FF005C', '#9D4EDD', '#7209B7', '#3A0CA3', '#4CC9F0'
   ];
   const color = colors[Math.floor(Math.random() * colors.length)];

   wordElement.style.fontSize = fontSize + 'px';
   wordElement.style.color = color;
   wordElement.style.setProperty('--rotation', `${rotation}deg`);

   lyricsWorld.appendChild(wordElement);
   const wordWidth = wordElement.offsetWidth;
   const wordHeight = wordElement.offsetHeight;

   let x, y;

   const timeToNext = (nextWordData && typeof nextWordData.start !== 'undefined') ?
      nextWordData.start - wordData.start :
      1000;
   const fastThreshold = 0.4;

   if (timeToNext < fastThreshold) {

      const radius = 200;
      const angle = Math.random() * 2 * Math.PI;
      x = lastWordPosition.x + Math.cos(angle) * radius;
      y = lastWordPosition.y + Math.sin(angle) * radius;
   } else {

      const padding = 30;
      const safeWidth = window.innerWidth - wordWidth - padding * 2;
      const safeHeight = window.innerHeight - wordHeight - padding * 2;
      x = Math.random() * safeWidth + padding;
      y = Math.random() * safeHeight + padding;
   }

   const padding = 30;
   x = Math.max(padding, Math.min(x, window.innerWidth - wordWidth - padding));
   y = Math.max(padding, Math.min(y, window.innerHeight - wordHeight - padding));

   lastWordPosition = {
      x,
      y
   };

   wordElement.style.left = x + 'px';
   wordElement.style.top = y + 'px';

   if (!isDragging) {
      targetX = -(x - window.innerWidth / 2 + wordWidth / 2);
      targetY = -(y - window.innerHeight / 2 + wordHeight / 2);
   }

   lyricsWorld.appendChild(wordElement);

   setTimeout(() => {
      wordElement.classList.add('visible');
   }, 10);

   setTimeout(() => {
      wordElement.classList.remove('visible');

      setTimeout(() => {
         if (wordElement.parentNode) {
            wordElement.parentNode.removeChild(wordElement);
         }
      }, 400);
   }, 1200);
}

const style = document.createElement('style');
style.textContent = `
    @keyframes bounce {
        0%, 100% { transform: translateY(0) rotate(var(--rotation, 0deg)); }
        50% { transform: translateY(-30px) rotate(var(--rotation, 0deg)); }
    }

    @keyframes spin {
        0% { transform: rotate(0deg) scale(1); }
        50% { transform: rotate(180deg) scale(1.2); }
        100% { transform: rotate(360deg) scale(1); }
    }

    @keyframes pulse {
        0%, 100% { transform: scale(1); opacity: 1; }
        50% { transform: scale(1.3); opacity: 0.8; }
    }

    @keyframes wiggle {
        0%, 100% { transform: rotate(-5deg); }
        25% { transform: rotate(5deg); }
        50% { transform: rotate(-5deg); }
        75% { transform: rotate(5deg); }
    }

    @keyframes fade {
        0% { opacity: 0; transform: scale(0.5); }
        50% { opacity: 1; transform: scale(1.2); }
        100% { opacity: 0; transform: scale(1); }
    }
`;
document.head.appendChild(style);

function populateTimedWordsEditor(words) {
   timedWordsContainer.innerHTML = '';
   if (!Array.isArray(words)) return;
   words.forEach((wordData, index) => {
      const item = document.createElement('div');
      item.className = 'timed-word-item';

      const timestamp = document.createElement('span');
      timestamp.className = 'timestamp';
      timestamp.textContent = wordData.start.toFixed(2);

      const input = document.createElement('input');
      input.type = 'text';
      input.className = 'word-input';
      input.value = wordData.word;
      input.dataset.index = index;

      item.appendChild(timestamp);
      item.appendChild(input);
      timedWordsContainer.appendChild(item);
   });
}

loadPonkBtn.addEventListener('click', () => {
   ponkFileInput.click();
});

ponkFileInput.addEventListener('change', (e) => {
   const file = e.target.files[0];
   if (!file) return;

   const reader = new FileReader();
   reader.onload = (event) => {
      try {
         ponkFileContent = JSON.parse(event.target.result);
         
         // Display modal to ensure user-gesture compliance
         if (ponkModal && ponkModalFilename) {
            ponkModalFilename.textContent = ponkFileContent.metadata.originalFilename;
            ponkModal.classList.add('active');
         }

         if (loadPonkBtn) loadPonkBtn.classList.add('loaded');
      } catch (error) {
         alert('Invalid .ponk file format.');
         ponkFileContent = null;
      }

      ponkFileInput.value = '';
   };
   reader.readAsText(file);
});

if (ponkModalSelectBtn) {
   ponkModalSelectBtn.addEventListener('click', () => {
      if (fileInput) fileInput.click();
      if (ponkModal) ponkModal.classList.remove('active');
   });
}

downloadPonkBtn.addEventListener('click', () => {
   if (!audioData) {
      alert('No audio data to save.');
      return;
   }

   const ponkData = {
      metadata: audioData.metadata,
      lyrics: audioData.lyrics,
      words: audioData.words,
      lyrics_source: audioData.lyrics_source
   };

   const blob = new Blob([JSON.stringify(ponkData, null, 2)], {
      type: 'application/json'
   });
   const url = URL.createObjectURL(blob);
   const a = document.createElement('a');
   a.href = url;

   const baseFilename = audioData.metadata.originalFilename.split('.').slice(0, -1).join('.');
   a.download = `${baseFilename}.ponk`;
   document.body.appendChild(a);
   a.click();
   document.body.removeChild(a);
   URL.revokeObjectURL(url);
});

function startAnimationLoop() {
   function loop() {

      const lerpFactor = 0.04;
      currentX += (targetX - currentX) * lerpFactor;
      currentY += (targetY - currentY) * lerpFactor;
      currentScale += (targetScale - currentScale) * lerpFactor;

      if (!isDragging) {
         lyricsWorld.style.transform = `translate(${currentX}px, ${currentY}px) scale(${currentScale})`;
      }

      animationFrameId = requestAnimationFrame(loop);
   }
   loop();
}

function stopAnimationLoop() {
   if (animationFrameId) {
      cancelAnimationFrame(animationFrameId);
   }
}

function triggerAutoZoom() {
   if (!isManualZooming) {
      targetScale = Math.random() * 1.2 + 0.6;
   }
}

function handleZoom(event) {
   return;
}

function handleDragStart(event) {
   return;
}

function handleDragMove(event) {
   return;
}

function handleDragEnd() {
   return;
}
