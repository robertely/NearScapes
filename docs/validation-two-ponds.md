# Validation: Two Ponds Walk.mp3

NearScapes was exercised against the 49:11.736 MP3 supplied during development.

Observed source probe:

- codec: MP3
- sample rate: 48,000 Hz
- channels: stereo
- duration: 2951.736 s
- source tags: `title=260925_0098.mp3`, `album=TASCAM DR-05`

The current `slate-tone` DSP analyzer finds exactly two sustained ~1 kHz markers:

| Marker | Start | End | Tone-to-guard |
| --- | ---: | ---: | ---: |
| 1 | 547.375 s | 549.375 s | ~46.0 dB |
| 2 | 2936.025 s | 2938.025 s | ~45.5 dB |

These correspond to approximately 09:07.375 and 48:56.025 in the recording and match the markers identified manually. An earlier detector revision also produced a false positive near 24:40; the current detector rejects it by requiring the 1 kHz component to be both narrow-band dominant and sufficiently dominant relative to total frame energy, while also enforcing the expected marker duration.

The upload/API path exposes the same analyzer results through:

```
GET /api/sources/{source_id}/analysis
```

The browser displays completed analyzer events on the shared recording timeline.

## Location

The source file itself contains no GPS/ISO-6709 metadata. NearScapes therefore leaves `recording_metadata.location` unset rather than inferring a location from the filename, server location, or development context.

The spoken slate in this recording contains location information, but recovering that belongs to the next ASR/slate-parser pass. That pass should populate latitude/longitude with explicit provenance from the bracketed speech.

## Verification

At repository head, GitHub CI passes lint and all unit tests, including:

- correct 2-second 1 kHz marker detection
- rejection of wrong frequency and wrong duration
- rejection of weak 1 kHz energy embedded in louder non-slate audio
- embedded location parsing
- waveform normalization
