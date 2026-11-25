"""
marker_generator.py
Generates Premiere Pro XML markers and LosslessCut CSV segment files from
scraped event data — ported from the WS Marker Tool's pages/3, 4 and 5,
so the offset math, colour mapping, and file formats match exactly what
that tool already produces and what you're used to importing.

Core model (unchanged from the Marker Tool):
  - Each event's WhoScored `minute` is continuous across the match (second
    half events read ~45+, not reset to 0), so `elapsed` = seconds since
    THIS PERIOD's own kickoff = (minute - period_base_minutes)*60 + second.
  - `kickoff` = a timecode YOU enter, in the actual video FILE, of where
    that period's kickoff lands (e.g. "00:01:34" if there's a pre-match
    intro before kickoff in the broadcast recording).
  - abs_secs = kickoff + elapsed = the position in your video file.
  - Broadcast cut offsets handle mid-half discontinuities (e.g. a
    hydration break cut from the broadcast) — for events after a given
    elapsed threshold within a half, the position shifts by the
    difference between where the file actually resumes and where it
    "should" be if nothing had been cut.
"""

import csv
import io
import re
import uuid
import datetime

PERIOD_BASE_MINS = {
    "FirstHalf": 0,
    "SecondHalf": 45,
    "ExtraTimeFirstHalf": 90,
    "FirstPeriodOfExtraTime": 90,
    "ExtraTimeSecondHalf": 105,
    "SecondPeriodOfExtraTime": 105,
    "PenaltyShootout": 120,
}


def parse_tc_to_secs(tc_str: str) -> float:
    """Accepts 'HH:MM:SS', 'MM:SS', or a bare number of seconds."""
    tc_str = (tc_str or "").strip()
    if not tc_str:
        return 0.0
    if re.match(r"^\d+(\.\d+)?$", tc_str):
        return float(tc_str)
    parts = tc_str.split(":")
    try:
        if len(parts) >= 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        pass
    return 0.0


def event_to_abs_secs(ev: dict, tc1_s, tc2_s, tc_et1_s, tc_et2_s, tc_pso_s, cut_offsets=None) -> float:
    """
    Converts one event's WhoScored minute/second into an absolute second
    position in your video file, given kickoff timecodes per period and
    any broadcast cut offsets for that period.
    cut_offsets: list of (period, cut_match_s, cut_file_s) tuples — where
    cut_match_s is "seconds into this half when play resumed" and
    cut_file_s is "the file timecode at that same moment", both derived
    from scrubbing your actual footage.
    """
    period = ev["period"]
    base_mins = PERIOD_BASE_MINS.get(period, 0)
    elapsed = (ev["minute"] - base_mins) * 60 + ev["second"]
    if elapsed < 0:
        elapsed = 0
    kickoff = {
        "FirstHalf": tc1_s,
        "SecondHalf": tc2_s,
        "ExtraTimeFirstHalf": tc_et1_s,
        "FirstPeriodOfExtraTime": tc_et1_s,
        "ExtraTimeSecondHalf": tc_et2_s,
        "SecondPeriodOfExtraTime": tc_et2_s,
        "PenaltyShootout": tc_pso_s,
    }.get(period, tc1_s)
    raw = kickoff + elapsed
    if cut_offsets:
        relevant = [c for c in cut_offsets if c[0] == period]
        for _, cut_match_s, cut_file_s in sorted(relevant, key=lambda c: c[1]):
            if elapsed > cut_match_s:
                raw = kickoff + elapsed + (cut_file_s - (kickoff + cut_match_s))
    return raw


def secs_to_frames(secs: float, fps_int: int) -> int:
    return int(round(secs * fps_int))


def secs_to_tc(secs: float) -> str:
    secs = max(0, secs)
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    s = int(secs % 60)
    return f"{h:02d}:{m:02d}:{s:02d}:00"


def xml_esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# ── Colours (pproColor = ABGR uint32, matches real Premiere XML) ──────────
def _abgr(r, g, b, a=255):
    return (a << 24) | (b << 16) | (g << 8) | r


PPRO_COLORS = {
    "blue": _abgr(0x42, 0x8D, 0xFC),
    "white": _abgr(0xFF, 0xFF, 0xFF),
    "orange": _abgr(0xE9, 0x6F, 0x24),
    "yellow": _abgr(0xD0, 0xA1, 0x2B),
    "red": _abgr(0xE8, 0x31, 0x31),
    "green": _abgr(0x53, 0xD2, 0x5E),
    "cyan": _abgr(0x26, 0xC4, 0xD3),
    "magenta": _abgr(0xD3, 0x26, 0xC4),
    "purple": _abgr(0x8E, 0x26, 0xD3),
}

TYPE_TO_COLOR = {
    "Goal": "red",
    "SavedShot": "orange",
    "MissedShot": "orange",
    "BlockedShot": "orange",
    "ShotOnPost": "orange",
    "AttemptSaved": "orange",
    "Pass": "blue",
    "TakeOn": "green",
    "Foul": "yellow",
    "Foul Drawn": "yellow",
    "Card": "red",
    "Clearance": "white",
    "Interception": "cyan",
    "Tackle": "cyan",
    "Carry": "blue",
    "BallTouch": "white",
    "Save": "green",
    "Block": "cyan",
    "Error": "magenta",
    "Dispossessed": "yellow",
    # Additions beyond the original Marker Tool's list, covering more of
    # our own scraper's raw type vocabulary — anything not listed here
    # just falls back to DEFAULT_COLOR rather than erroring.
    "SubstitutionOn": "purple",
    "SubstitutionOff": "purple",
    "CornerAwarded": "yellow",
    "BallRecovery": "cyan",
}
DEFAULT_COLOR = "blue"

FPS_OPTIONS = {
    "23.976 fps": {"timebase": 24, "ntsc": "TRUE", "fps": 23.976},
    "24 fps": {"timebase": 24, "ntsc": "FALSE", "fps": 24},
    "25 fps": {"timebase": 25, "ntsc": "FALSE", "fps": 25},
    "29.97 fps": {"timebase": 30, "ntsc": "TRUE", "fps": 29.97},
    "30 fps": {"timebase": 30, "ntsc": "FALSE", "fps": 30},
    "50 fps": {"timebase": 50, "ntsc": "FALSE", "fps": 50},
    "59.94 fps": {"timebase": 60, "ntsc": "TRUE", "fps": 59.94},
    "60 fps": {"timebase": 60, "ntsc": "FALSE", "fps": 60},
}


def merge_overlapping(enriched: list, pre_buffer: float, post_buffer: float) -> list:
    """Merges events whose buffered [start,end] windows overlap into single segments."""
    if not enriched:
        return []
    windows = sorted(
        [(max(0.0, ev["_abs_secs"] - pre_buffer), ev["_abs_secs"] + post_buffer, ev) for ev in enriched],
        key=lambda x: x[0],
    )
    merged = []
    cur_start, cur_end, cur_evs = windows[0][0], windows[0][1], [windows[0][2]]
    for start, end, ev in windows[1:]:
        if start <= cur_end:
            cur_end = max(cur_end, end)
            cur_evs.append(ev)
        else:
            merged.append({"start": cur_start, "end": cur_end, "events": cur_evs})
            cur_start, cur_end, cur_evs = start, end, [ev]
    merged.append({"start": cur_start, "end": cur_end, "events": cur_evs})
    return merged


def enrich_events(events: list, tc1_s, tc2_s, tc_et1_s, tc_et2_s, tc_pso_s, cut_offsets=None) -> list:
    """Attaches _abs_secs to each event dict (returns new dicts, doesn't mutate input)."""
    enriched = []
    for ev in events:
        e = dict(ev)
        e["_abs_secs"] = event_to_abs_secs(ev, tc1_s, tc2_s, tc_et1_s, tc_et2_s, tc_pso_s, cut_offsets=cut_offsets)
        enriched.append(e)
    return enriched


def generate_xml(
    enriched: list,
    seq_name: str,
    fps_key: str,
    pre_buffer: float,
    post_buffer: float,
    merge_overlaps: bool = False,
) -> str:
    """
    Ported verbatim (structure, tags, and attribute values) from the WS
    Marker Tool's Export XML page — not reconstructed from memory. This
    matters because Premiere's XML import is picky about the exact
    <format>/<samplecharacteristics>/<codec> block being present; an
    abbreviated version can silently fail to import cleanly.
    """
    fp = FPS_OPTIONS[fps_key]
    fps_int = fp["timebase"]
    ntsc = fp["ntsc"]

    if enriched:
        last_secs = max(ev["_abs_secs"] for ev in enriched)
        seq_frames = secs_to_frames(last_secs + post_buffer + 10, fps_int)
    else:
        seq_frames = fps_int * 60 * 60 * 3

    seq_uuid = uuid.uuid4().hex
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H-%M-%S")
    safe_name = xml_esc(seq_name)

    marker_lines = []
    marker_lines.append(f"""    <marker>
      <comment>- Start -</comment>
      <name/>
      <in>0</in>
      <out>-1</out>
      <pproColor>{PPRO_COLORS["blue"]}</pproColor>
    </marker>""")

    if merge_overlaps:
        segments = merge_overlapping(enriched, pre_buffer, post_buffer)
        for seg in segments:
            evs = seg["events"]
            in_frame = secs_to_frames(seg["start"], fps_int)
            out_frame = secs_to_frames(seg["end"], fps_int)
            types_str = " + ".join(sorted({e["type"] for e in evs}))
            name = xml_esc(types_str)
            comment = xml_esc(" | ".join(
                f"{e['type']} {e['minute']}'{e['second']:02d}s ({e['outcome']})"
                for e in sorted(evs, key=lambda e: e["_abs_secs"])
            ))
            color_name = TYPE_TO_COLOR.get(evs[0]["type"], DEFAULT_COLOR)
            ppro_color = PPRO_COLORS.get(color_name, PPRO_COLORS[DEFAULT_COLOR])
            color_tag = f"\n      <pproColor>{ppro_color}</pproColor>" if color_name != "green" else ""
            marker_lines.append(f"""    <marker>
      <comment>{comment}</comment>
      <name>{name}</name>
      <in>{in_frame}</in>
      <out>{out_frame}</out>{color_tag}
    </marker>""")
    else:
        segments = [{"start": max(0, ev["_abs_secs"] - pre_buffer),
                     "end": ev["_abs_secs"] + post_buffer,
                     "events": [ev]} for ev in enriched]
        for ev in enriched:
            in_frame = secs_to_frames(max(0, ev["_abs_secs"] - pre_buffer), fps_int)
            out_frame = secs_to_frames(ev["_abs_secs"] + post_buffer, fps_int)
            name = xml_esc(ev["type"])
            comment = xml_esc(f"{ev['playerName']} ({ev['outcome']}) | {ev['period']} {ev['minute']}'{ev['second']:02d}s")
            color_name = TYPE_TO_COLOR.get(ev["type"], DEFAULT_COLOR)
            ppro_color = PPRO_COLORS.get(color_name, PPRO_COLORS[DEFAULT_COLOR])
            color_tag = f"\n      <pproColor>{ppro_color}</pproColor>" if color_name != "green" else ""
            marker_lines.append(f"""    <marker>
      <comment>{comment}</comment>
      <name>{name}</name>
      <in>{in_frame}</in>
      <out>{out_frame}</out>{color_tag}
    </marker>""")

    markers_block = "\n".join(marker_lines)

    clip_marker_lines = []
    clip_marker_lines.append(f"""            <marker>
              <comment>- Start -</comment>
              <name/>
              <in>0</in>
              <out>-1</out>
              <pproColor>{PPRO_COLORS["blue"]}</pproColor>
            </marker>""")

    if merge_overlaps:
        for seg in segments:
            evs = seg["events"]
            in_frame = secs_to_frames(seg["start"], fps_int)
            out_frame = secs_to_frames(seg["end"], fps_int)
            types_str = " + ".join(sorted({e["type"] for e in evs}))
            name = xml_esc(types_str)
            comment = xml_esc(" | ".join(
                f"{e['type']} {e['minute']}'{e['second']:02d}s ({e['outcome']})"
                for e in sorted(evs, key=lambda e: e["_abs_secs"])
            ))
            color_name = TYPE_TO_COLOR.get(evs[0]["type"], DEFAULT_COLOR)
            ppro_color = PPRO_COLORS.get(color_name, PPRO_COLORS[DEFAULT_COLOR])
            color_tag = f"\n              <pproColor>{ppro_color}</pproColor>" if color_name != "green" else ""
            clip_marker_lines.append(f"""            <marker>
              <comment>{comment}</comment>
              <name>{name}</name>
              <in>{in_frame}</in>
              <out>{out_frame}</out>{color_tag}
            </marker>""")
    else:
        for ev in enriched:
            in_frame = secs_to_frames(max(0, ev["_abs_secs"] - pre_buffer), fps_int)
            out_frame = secs_to_frames(ev["_abs_secs"] + post_buffer, fps_int)
            name = xml_esc(ev["type"])
            comment = xml_esc(f"{ev['playerName']} ({ev['outcome']}) | {ev['period']} {ev['minute']}'{ev['second']:02d}s")
            color_name = TYPE_TO_COLOR.get(ev["type"], DEFAULT_COLOR)
            ppro_color = PPRO_COLORS.get(color_name, PPRO_COLORS[DEFAULT_COLOR])
            color_tag = f"\n              <pproColor>{ppro_color}</pproColor>" if color_name != "green" else ""
            clip_marker_lines.append(f"""            <marker>
              <comment>{comment}</comment>
              <name>{name}</name>
              <in>{in_frame}</in>
              <out>{out_frame}</out>{color_tag}
            </marker>""")

    clip_markers = "\n".join(clip_marker_lines)

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="4">
  <sequence id="sequence" TL.SQAudioVisibleBase="0" TL.SQVideoVisibleBase="0" TL.SQVisibleBaseTime="0" TL.SQAVDividerPosition="0.5" TL.SQHideShyTracks="0" TL.SQHeaderWidth="292" Monitor.ProgramZoomOut="0" Monitor.ProgramZoomIn="0" TL.SQTimePerPixel="0.19999999999999998" MZ.EditLine="0" MZ.Sequence.PreviewFrameSizeHeight="1080" MZ.Sequence.PreviewFrameSizeWidth="1920" MZ.Sequence.AudioTimeDisplayFormat="200" MZ.Sequence.PreviewRenderingClassID="1061109567" MZ.Sequence.PreviewRenderingPresetCodec="1634755439" MZ.Sequence.PreviewRenderingPresetPath="EncoderPresets/SequencePreview/795454d9-d3c2-429d-9474-923ab13b7018/QuickTime.epr" MZ.Sequence.PreviewUseMaxRenderQuality="false" MZ.Sequence.PreviewUseMaxBitDepth="false" MZ.Sequence.EditingModeGUID="795454d9-d3c2-429d-9474-923ab13b7018" MZ.Sequence.VideoTimeDisplayFormat="101" MZ.WorkOutPoint="4612930560000" MZ.WorkInPoint="0" explodedTracks="true">
    <uuid>{seq_uuid}</uuid>
    <duration>{seq_frames}</duration>
    <rate>
      <timebase>{fps_int}</timebase>
      <ntsc>{ntsc}</ntsc>
    </rate>
    <name>Marker - {safe_name} ({now_str})</name>
    <media>
      <video>
        <format>
          <samplecharacteristics>
            <rate>
              <timebase>{fps_int}</timebase>
              <ntsc>{ntsc}</ntsc>
            </rate>
            <codec>
              <name>Apple ProRes 422</name>
              <appspecificdata>
                <appname>Final Cut Pro</appname>
                <appmanufacturer>Apple Inc.</appmanufacturer>
                <appversion>7.0</appversion>
                <data>
                  <qtcodec>
                    <codecname>Apple ProRes 422</codecname>
                    <codectypename>Apple ProRes 422</codectypename>
                    <codecname>Apple ProRes 422</codecname>
                    <codectypecode>apcn</codectypecode>
                    <codecvendorcode>appl</codecvendorcode>
                    <spatialquality>1024</spatialquality>
                    <temporalquality>0</temporalquality>
                    <keyframerate>0</keyframerate>
                    <datarate>0</datarate>
                  </qtcodec>
                </data>
              </appspecificdata>
            </codec>
            <width>1920</width>
            <height>1080</height>
            <anamorphic>FALSE</anamorphic>
            <pixelaspectratio>square</pixelaspectratio>
            <fielddominance>none</fielddominance>
            <colordepth>24</colordepth>
          </samplecharacteristics>
        </format>
        <track TL.SQTrackShy="0" TL.SQTrackExpandedHeight="25" TL.SQTrackExpanded="0" MZ.TrackTargeted="0">
          <enabled>TRUE</enabled>
          <locked>FALSE</locked>
          <generatoritem id="clipitem-1">
            <name>Marker Color Matte ({now_str})</name>
            <enabled>TRUE</enabled>
            <duration>{seq_frames}</duration>
            <rate>
              <timebase>{fps_int}</timebase>
              <ntsc>{ntsc}</ntsc>
            </rate>
            <start>0</start>
            <end>{seq_frames}</end>
            <in>0</in>
            <out>{seq_frames}</out>
            <alphatype>none</alphatype>
            <effect>
              <name>Color</name>
              <effectid>Color</effectid>
              <effectcategory>Matte</effectcategory>
              <effecttype>generator</effecttype>
              <mediatype>video</mediatype>
              <parameter authoringApp="PremierePro">
                <parameterid>fillcolor</parameterid>
                <name>Color</name>
                <value>
                  <alpha>0</alpha>
                  <red>0</red>
                  <green>0</green>
                  <blue>0</blue>
                </value>
              </parameter>
            </effect>
            <filter>
              <effect>
                <name>Opacity</name>
                <effectid>opacity</effectid>
                <effectcategory>motion</effectcategory>
                <effecttype>motion</effecttype>
                <mediatype>video</mediatype>
                <pproBypass>false</pproBypass>
                <parameter authoringApp="PremierePro">
                  <parameterid>opacity</parameterid>
                  <name>opacity</name>
                  <valuemin>0</valuemin>
                  <valuemax>100</valuemax>
                  <value>0</value>
                </parameter>
              </effect>
            </filter>
{clip_markers}
          </generatoritem>
        </track>
      </video>
      <audio>
        <numOutputChannels>2</numOutputChannels>
        <format>
          <samplecharacteristics>
            <depth>16</depth>
            <samplerate>48000</samplerate>
          </samplecharacteristics>
        </format>
        <outputs>
          <groups>
            <index>1</index>
            <numchannels>1</numchannels>
            <downmix>0</downmix>
            <channel><index>1</index></channel>
          </groups>
          <groups>
            <index>2</index>
            <numchannels>1</numchannels>
            <downmix>0</downmix>
            <channel><index>2</index></channel>
          </groups>
        </outputs>
        <track TL.SQTrackAudioKeyframeStyle="0" TL.SQTrackShy="0" TL.SQTrackExpandedHeight="25" TL.SQTrackExpanded="0" MZ.TrackTargeted="1" PannerCurrentValue="0.5" PannerIsInverted="true" PannerStartKeyframe="-91445760000000000,0.5,0,0,0,0,0,0" PannerName="Balance" currentExplodedTrackIndex="0" totalExplodedTrackCount="2" premiereTrackType="Stereo">
          <enabled>TRUE</enabled>
          <locked>FALSE</locked>
          <outputchannelindex>1</outputchannelindex>
        </track>
        <track TL.SQTrackAudioKeyframeStyle="0" TL.SQTrackShy="0" TL.SQTrackExpandedHeight="25" TL.SQTrackExpanded="0" MZ.TrackTargeted="1" PannerCurrentValue="0.5" PannerIsInverted="true" PannerStartKeyframe="-91445760000000000,0.5,0,0,0,0,0,0" PannerName="Balance" currentExplodedTrackIndex="1" totalExplodedTrackCount="2" premiereTrackType="Stereo">
          <enabled>TRUE</enabled>
          <locked>FALSE</locked>
          <outputchannelindex>2</outputchannelindex>
        </track>
      </audio>
    </media>
    <timecode>
      <rate>
        <timebase>{fps_int}</timebase>
        <ntsc>{ntsc}</ntsc>
      </rate>
      <string>00:00:00:00</string>
      <frame>0</frame>
      <displayformat>NDF</displayformat>
    </timecode>
    <labels>
      <label2>Iris</label2>
    </labels>
    <logginginfo>
      <description/>
      <scene/>
      <shottake/>
      <lognote/>
      <good/>
      <originalvideofilename/>
      <originalaudiofilename/>
    </logginginfo>
{markers_block}
    <labels>
      <label2>Forest</label2>
    </labels>
  </sequence>
</xmeml>"""
    return xml


def generate_llc_csv(enriched: list, pre_buffer: float, post_buffer: float, merge_overlaps: bool = False) -> str:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["Start", "End", "Label"])
    if merge_overlaps:
        for seg in merge_overlapping(enriched, pre_buffer, post_buffer):
            evs = seg["events"]
            start = round(seg["start"], 3)
            end = round(seg["end"], 3)
            types = " + ".join(sorted({e["type"] for e in evs}))
            label = f"{types} - {evs[0]['playerName']} ({evs[0]['minute']}')"
            if len(evs) > 1:
                label += f" +{len(evs) - 1} more"
            writer.writerow([start, end, label])
    else:
        for ev in enriched:
            start = round(max(0.0, ev["_abs_secs"] - pre_buffer), 3)
            end = round(ev["_abs_secs"] + post_buffer, 3)
            label = f"{ev['type']} - {ev['playerName']} ({ev['minute']}')"
            writer.writerow([start, end, label])
    return output.getvalue()
