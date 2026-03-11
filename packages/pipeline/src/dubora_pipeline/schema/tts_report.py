"""
Schema: TTS Report - Per-segment synthesis results.

The TTS report captures detailed information about each synthesized segment:
- Raw duration (before processing)
- Trimmed duration (after silence removal)
- Final duration (after rate adjustment)
- Applied rate and status

This enables debugging and validation of the TTS phase.
"""
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


class TTSSegmentStatus(str, Enum):
    """Status of a TTS segment synthesis."""
    SUCCESS = "success"           # Segment fits within budget
    RATE_ADJUSTED = "rate_adjusted"  # Rate applied to fit budget
    EXTENDED = "extended"         # Used allow_extend_ms to fit
    FAILED = "failed"             # Could not fit even with max adjustments


@dataclass
class TTSSegmentReport:
    """
    Report for a single TTS segment synthesis.

    Timing fields (all in milliseconds):
    - utt_id: Utterance ID (matches DubManifest)
    - budget_ms: Original time budget from manifest
    - raw_ms: Raw TTS output duration (before processing)
    - trimmed_ms: Duration after silence trimming
    - final_ms: Final duration after rate adjustment
    - rate: Applied rate multiplier (1.0 = no change)

    Status:
    - status: TTSSegmentStatus enum value
    - output_path: Relative path to segment WAV file

    Optional:
    - error: Error message if status is FAILED
    """
    utt_id: str
    budget_ms: int
    raw_ms: int
    trimmed_ms: int
    final_ms: int
    rate: float
    status: TTSSegmentStatus
    output_path: str
    error: Optional[str] = None


@dataclass
class TTSReport:
    """
    Complete TTS synthesis report.

    Fields:
    - audio_duration_ms: Total audio duration (from manifest, for reference)
    - segments_dir: Directory containing segment WAV files
    - segments: List of TTSSegmentReport objects
    - total_segments: Total number of segments
    - success_count: Number of successful segments
    - failed_count: Number of failed segments
    """
    audio_duration_ms: int
    segments_dir: str
    segments: List[TTSSegmentReport]

    @property
    def total_segments(self) -> int:
        return len(self.segments)

    @property
    def success_count(self) -> int:
        return sum(1 for s in self.segments if s.status != TTSSegmentStatus.FAILED)

    @property
    def failed_count(self) -> int:
        return sum(1 for s in self.segments if s.status == TTSSegmentStatus.FAILED)

    @property
    def all_succeeded(self) -> bool:
        return self.failed_count == 0


def tts_report_to_dict(report: TTSReport) -> dict:
    """Serialize TTSReport to dict for JSON output."""
    return {
        "audio_duration_ms": report.audio_duration_ms,
        "segments_dir": report.segments_dir,
        "total_segments": report.total_segments,
        "success_count": report.success_count,
        "failed_count": report.failed_count,
        "segments": [
            {
                "utt_id": s.utt_id,
                "budget_ms": s.budget_ms,
                "raw_ms": s.raw_ms,
                "trimmed_ms": s.trimmed_ms,
                "final_ms": s.final_ms,
                "rate": s.rate,
                "status": s.status.value,
                "output_path": s.output_path,
                "error": s.error,
            }
            for s in report.segments
        ],
    }


def tts_report_from_dict(data: dict) -> TTSReport:
    """Deserialize TTSReport from dict (JSON input)."""
    segments = []
    for s in data["segments"]:
        segments.append(
            TTSSegmentReport(
                utt_id=s["utt_id"],
                budget_ms=s["budget_ms"],
                raw_ms=s["raw_ms"],
                trimmed_ms=s["trimmed_ms"],
                final_ms=s["final_ms"],
                rate=s["rate"],
                status=TTSSegmentStatus(s["status"]),
                output_path=s["output_path"],
                error=s.get("error"),
            )
        )
    return TTSReport(
        audio_duration_ms=data["audio_duration_ms"],
        segments_dir=data["segments_dir"],
        segments=segments,
    )
