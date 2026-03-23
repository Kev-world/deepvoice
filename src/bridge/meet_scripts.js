(function() {
    'use strict';

    const WS_URL = window.__DEEPVOICE_WS_URL || 'ws://localhost:8000/api/bridge/ws';
    const SAMPLE_RATE = 48000;
    const BUFFER_SIZE = 4096;

    let ws = null;
    let audioContext = null;
    let capturedStreams = new Set();
    let agentAudioSource = null;
    let agentMediaStream = null;

    // ─── WebSocket connection ─────────────────────────────────

    function connectWebSocket() {
        try {
            ws = new WebSocket(WS_URL);
        } catch (e) {
            console.warn('[DeepVoice] WebSocket connection failed, retrying in 3s...', e);
            setTimeout(connectWebSocket, 3000);
            return;
        }
        ws.binaryType = 'arraybuffer';

        ws.onopen = () => {
            console.log('[DeepVoice] WebSocket connected to audio bridge');
            ws.send(JSON.stringify({ type: 'hello', sampleRate: SAMPLE_RATE }));
        };

        ws.onmessage = (event) => {
            if (event.data instanceof ArrayBuffer) {
                playAgentAudio(event.data);
            } else {
                const msg = JSON.parse(event.data);
                if (msg.type === 'ready') {
                    console.log('[DeepVoice] Audio bridge ready');
                }
            }
        };

        ws.onclose = () => {
            console.log('[DeepVoice] WebSocket closed, reconnecting in 3s...');
            setTimeout(connectWebSocket, 3000);
        };

        ws.onerror = (err) => {
            console.error('[DeepVoice] WebSocket error:', err);
        };
    }

    // ─── Audio Context setup ──────────────────────────────────

    function ensureAudioContext() {
        if (!audioContext || audioContext.state === 'closed') {
            audioContext = new AudioContext({ sampleRate: SAMPLE_RATE });
        }
        if (audioContext.state === 'suspended') {
            audioContext.resume();
        }
        return audioContext;
    }

    // ─── Capture remote audio from Google Meet ────────────────
    // Intercept RTCPeerConnection via prototype patching (works even
    // when window.RTCPeerConnection is non-configurable / read-only).

    const OrigRTCPC = window.RTCPeerConnection;
    const origAddTrack = RTCPeerConnection.prototype.addTrack;
    const origSetRemoteDescription = RTCPeerConnection.prototype.setRemoteDescription;

    // Patch ontrack at the prototype level so every new peer connection
    // automatically has our listener without replacing the constructor.
    const origOntrackDescriptor = Object.getOwnPropertyDescriptor(
        RTCPeerConnection.prototype, 'ontrack'
    );

    // We listen on every peer connection via addEventListener instead.
    // Intercept setRemoteDescription — by the time it's called the
    // connection object exists, so we attach our track listener.
    RTCPeerConnection.prototype.setRemoteDescription = function(...args) {
        if (!this.__deepvoice_patched) {
            this.__deepvoice_patched = true;
            this.addEventListener('track', (event) => {
                if (event.track.kind === 'audio') {
                    const stream = event.streams[0] || new MediaStream([event.track]);
                    const streamId = stream.id;
                    if (!capturedStreams.has(streamId)) {
                        capturedStreams.add(streamId);
                        console.log('[DeepVoice] Capturing remote audio stream:', streamId);
                        captureAudioStream(stream);
                    }
                }
            });
        }
        return origSetRemoteDescription.apply(this, args);
    };

    function captureAudioStream(stream) {
        const ctx = ensureAudioContext();
        const source = ctx.createMediaStreamSource(stream);
        const processor = ctx.createScriptProcessor(BUFFER_SIZE, 1, 1);

        processor.onaudioprocess = (e) => {
            if (ws && ws.readyState === WebSocket.OPEN) {
                const inputData = e.inputBuffer.getChannelData(0);
                const int16Data = float32ToInt16(inputData);
                ws.send(int16Data.buffer);
            }
        };

        source.connect(processor);
        processor.connect(ctx.destination);
    }

    // ─── Play agent audio into Google Meet ────────────────────

    function setupAgentAudioPlayback() {
        const ctx = ensureAudioContext();
        const dest = ctx.createMediaStreamDestination();
        agentMediaStream = dest.stream;
        agentAudioSource = dest;
        console.log('[DeepVoice] Agent audio playback stream created');
    }

    function playAgentAudio(arrayBuffer) {
        if (!audioContext || !agentAudioSource) return;

        const ctx = audioContext;
        const int16Array = new Int16Array(arrayBuffer);
        const float32Array = int16ToFloat32(int16Array);

        const audioBuffer = ctx.createBuffer(1, float32Array.length, SAMPLE_RATE);
        audioBuffer.getChannelData(0).set(float32Array);

        const bufferSource = ctx.createBufferSource();
        bufferSource.buffer = audioBuffer;
        bufferSource.connect(agentAudioSource);
        bufferSource.start();
    }

    // ─── Override getUserMedia for mic injection ──────────────

    const origGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);

    navigator.mediaDevices.getUserMedia = async function(constraints) {
        if (constraints && constraints.audio && agentMediaStream) {
            console.log('[DeepVoice] Intercepting getUserMedia - returning agent audio stream');

            if (constraints.video) {
                const realStream = await origGetUserMedia({ video: constraints.video });
                const combinedStream = new MediaStream();
                realStream.getVideoTracks().forEach(t => combinedStream.addTrack(t));
                agentMediaStream.getAudioTracks().forEach(t => combinedStream.addTrack(t));
                return combinedStream;
            }

            return agentMediaStream;
        }

        return origGetUserMedia(constraints);
    };

    // ─── PCM conversion utilities ─────────────────────────────

    function float32ToInt16(float32Array) {
        const int16Array = new Int16Array(float32Array.length);
        for (let i = 0; i < float32Array.length; i++) {
            const s = Math.max(-1, Math.min(1, float32Array[i]));
            int16Array[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        return int16Array;
    }

    function int16ToFloat32(int16Array) {
        const float32Array = new Float32Array(int16Array.length);
        for (let i = 0; i < int16Array.length; i++) {
            float32Array[i] = int16Array[i] / (int16Array[i] < 0 ? 0x8000 : 0x7FFF);
        }
        return float32Array;
    }

    // ─── Initialize ───────────────────────────────────────────
    // Prototype patches above are already active.
    // Defer WebSocket + AudioContext until the page is interactive.

    function init() {
        setupAgentAudioPlayback();
        connectWebSocket();
        console.log('[DeepVoice] Audio bridge initialized');
    }

    if (document.readyState === 'complete' || document.readyState === 'interactive') {
        init();
    } else {
        document.addEventListener('DOMContentLoaded', init);
    }
})();
