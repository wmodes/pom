"""The shape of the audio the listening side handles, in one place.

Whisper takes 16 kHz mono 16-bit audio, so that is what the microphone is asked for and what a recording must be.
The audio travels in small frames. Every module in `pom.listening` that cuts, counts or converts audio takes these
numbers from here, so they cannot disagree.
"""
SAMPLE_RATE = 16000                                    # samples a second
SAMPLE_BYTES = 2                                       # 16-bit, one channel
BYTES_PER_SECOND = SAMPLE_RATE * SAMPLE_BYTES
FRAME_MS = 30                                          # one frame is 30 milliseconds of sound
FRAME_SECONDS = FRAME_MS / 1000
FRAME_BYTES = SAMPLE_RATE * FRAME_MS // 1000 * SAMPLE_BYTES
