document.getElementById('dropZone').onclick = () => document.getElementById('fileInput').click();
document.getElementById('fileInput').onchange = e => uploadFile(e.target.files[0]);

function uploadFile(file) {
  const formData = new FormData();
  formData.append('file', file);

  document.getElementById('progress').style.display = 'block';
  document.getElementById('result').style.display = 'none';

  fetch('/upload', { method: 'POST', body: formData })
    .then(r => r.json())
    .then(data => {
      document.getElementById('progress').style.display = 'none';
      if (data.success) {
        renderWords(data.words);
        document.getElementById('downloadBtn').href = data.srt_url;
        document.getElementById('result').style.display = 'block';
      }
    });
}

function renderWords(words) {
  const container = document.getElementById('wordContainer');
  container.innerHTML = '';
  words.forEach((w, i) => {
    const span = document.createElement('span');
    span.textContent = w.text + ' ';
    span.className = 'word-highlight';
    span.dataset.start = w.start;
    container.appendChild(span);

    if ((i + 1) % 12 === 0) container.appendChild(document.createElement('br'));
  });
}