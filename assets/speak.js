(function () {
  'use strict';

  var SPEAKER_SVG =
    '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">' +
    '<path fill="currentColor" d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/>' +
    '</svg>';

  var currentAudio = null;
  var currentBtn = null;
  var synthUtterance = null;

  function siteAudioBase() {
    var forced = document.querySelector('script[src*="speak.js"]');
    if (forced && forced.getAttribute('data-audio-base')) {
      return forced.getAttribute('data-audio-base').replace(/\/?$/, '/');
    }
    // days/*.html → ../audio/
    try {
      return new URL('../audio/', window.location.href).href;
    } catch (e) {
      return '../audio/';
    }
  }

  function speakText(word) {
    return String(word || '').replace(/-/g, ' ').trim();
  }

  function slugOf(word) {
    return String(word || '').trim().toLowerCase();
  }

  function stopAll() {
    if (currentAudio) {
      try { currentAudio.pause(); currentAudio.currentTime = 0; } catch (e) {}
      currentAudio = null;
    }
    if (window.speechSynthesis) {
      try { window.speechSynthesis.cancel(); } catch (e) {}
    }
    synthUtterance = null;
    if (currentBtn) {
      currentBtn.classList.remove('is-playing');
      currentBtn.setAttribute('aria-pressed', 'false');
      currentBtn = null;
    }
  }

  function setPlaying(btn, on) {
    if (!btn) return;
    btn.classList.toggle('is-playing', !!on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  }

  function fallbackSpeak(text, btn) {
    if (!window.speechSynthesis) {
      setPlaying(btn, false);
      currentBtn = null;
      return;
    }
    var u = new SpeechSynthesisUtterance(String(text || ''));
    u.lang = 'en-US';
    u.rate = 0.92;
    synthUtterance = u;
    u.onend = u.onerror = function () {
      setPlaying(btn, false);
      if (currentBtn === btn) currentBtn = null;
      synthUtterance = null;
    };
    try {
      var voices = window.speechSynthesis.getVoices() || [];
      var us = voices.find(function (v) { return /en-US/i.test(v.lang) && /neural|enhanced|google|samantha|jenny|guy|aria/i.test(v.name); })
        || voices.find(function (v) { return /en-US/i.test(v.lang); });
      if (us) u.voice = us;
    } catch (e) {}
    window.speechSynthesis.speak(u);
  }

  /** Play a path relative to audio/ (e.g. "abandon.mp3" or "ex/abcd.mp3"). */
  function playRelative(relPath, fallbackText, btn) {
    if (!relPath) return;

    if (currentBtn === btn && (currentAudio || synthUtterance)) {
      stopAll();
      return;
    }

    stopAll();
    currentBtn = btn;
    setPlaying(btn, true);

    var base = siteAudioBase();
    var url = base + String(relPath).replace(/^\//, '');
    var audio = new Audio();
    currentAudio = audio;
    audio.preload = 'auto';

    var usedFallback = false;
    function toFallback() {
      if (usedFallback) return;
      usedFallback = true;
      currentAudio = null;
      fallbackSpeak(fallbackText, btn);
    }

    audio.addEventListener('ended', function () {
      setPlaying(btn, false);
      if (currentBtn === btn) currentBtn = null;
      currentAudio = null;
    });
    audio.addEventListener('error', toFallback);
    audio.src = url;
    var p = audio.play();
    if (p && typeof p.then === 'function') {
      p.catch(toFallback);
    }
  }

  function playWord(word, btn) {
    var slug = slugOf(word);
    if (!slug) return;
    playRelative(encodeURIComponent(slug) + '.mp3', speakText(word), btn);
  }

  function playExample(enEl, btn) {
    var rel = enEl.getAttribute('data-audio');
    var text = (enEl.textContent || '').replace(/\s+/g, ' ').trim();
    if (!rel) {
      // No patched attr — fall back to browser TTS only
      if (currentBtn === btn && synthUtterance) {
        stopAll();
        return;
      }
      stopAll();
      currentBtn = btn;
      setPlaying(btn, true);
      fallbackSpeak(text, btn);
      return;
    }
    playRelative(rel, text, btn);
  }

  function makeSpeakBtn(ariaLabel) {
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'speak-btn';
    btn.setAttribute('aria-label', ariaLabel);
    btn.setAttribute('title', ariaLabel);
    btn.setAttribute('aria-pressed', 'false');
    btn.innerHTML = SPEAKER_SVG;
    return btn;
  }

  function enhanceCard(head) {
    if (!head || head.querySelector('.speak-btn:not(.speak-ex)')) return;
    var wordEl = head.querySelector('.word');
    if (!wordEl) return;
    var word = (wordEl.textContent || '').trim();
    if (!word) return;

    var btn = makeSpeakBtn('播放读音');
    btn.dataset.word = word;

    if (wordEl.nextSibling) {
      head.insertBefore(btn, wordEl.nextSibling);
    } else {
      head.appendChild(btn);
    }

    btn.addEventListener('click', function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      playWord(word, btn);
    });
  }

  function enhanceExample(enEl) {
    if (!enEl) return;
    var ex = enEl.closest ? enEl.closest('.ex') : null;
    if (!ex) return;
    if (ex.querySelector('.speak-btn.speak-ex')) return;

    var btn = makeSpeakBtn('播放例句');
    btn.classList.add('speak-ex');

    // Wrap <en> + button in a flex row so Quiet Paper stays one English line + icon
    var wrap = document.createElement('div');
    wrap.className = 'ex-en-row';
    enEl.parentNode.insertBefore(wrap, enEl);
    wrap.appendChild(enEl);
    wrap.appendChild(btn);

    btn.addEventListener('click', function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      playExample(enEl, btn);
    });
  }

  function init() {
    document.querySelectorAll('.card .head, .head').forEach(enhanceCard);
    document.querySelectorAll('.ex en').forEach(enhanceExample);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  if (window.speechSynthesis) {
    try { window.speechSynthesis.getVoices(); } catch (e) {}
    window.speechSynthesis.addEventListener('voiceschanged', function () {});
  }
})();
