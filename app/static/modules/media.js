// MediaRecorder mime negotiation, shared by the two recording surfaces (the
// audio audit and the product-search dictation).

// iOS Safari's MediaRecorder only produces audio/mp4 — picking a supported
// type (and labelling the blob/filename to match) is what stops whisper from
// choking on mp4 bytes mislabelled as .webm. Mirrors voice-transcriber.
export function pickAudioMime() {
  if (!("MediaRecorder" in window)) return "";
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4;codecs=mp4a.40.2", "audio/mp4"];
  for (const m of candidates) {
    if (MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(m)) return m;
  }
  return "";
}
