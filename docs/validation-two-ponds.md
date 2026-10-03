# Validation: Two Ponds Walk.mp3

NearScapes was exercised against the 49:11.736 MP3 supplied during development.

Observed source probe:

- codec: MP3
- sample rate: 48,000 Hz
- channels: stereo
- duration: 2951.736 s
- source tags: `title=260925_0098.mp3`, `album=TASCAM DR-05`

The `slate-tone` analyzer found two sustained ~1 kHz markers:

| Marker | Start | End | Frequency |
| --- | ---: | ---: | ---: |
| 1 | 547.325 s | 549.325 s | ~1000 Hz |
| 2 | 2936.000 s | 2938.000 s | ~1000 Hz |

These correspond to approximately 09:07.325 and 48:56.000 in the recording and match the markers identified manually earlier.

A direct upload/API integration run against the same source returned HTTP 200 with `beep_count=2` and those timestamps in the first-slice implementation.

Location remains unavailable from the file itself: its embedded tags contain no GPS/ISO-6709 fields. NearScapes therefore leaves location unset rather than inferring it from filename, server location, or unrelated context. The next slate-ASR/parser pass should populate latitude/longitude from the spoken slate with explicit provenance.
