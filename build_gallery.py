"""Build a standalone reference gallery. Requires Python 3.10+."""
import argparse
import hashlib
import html
import json
import math
from datetime import datetime
from fractions import Fraction
import os
import re
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from urllib.request import url2pathname

try:
    from PIL import Image
except ImportError:
    Image = None

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "config.json"
EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".svg", ".bmp"}
JPEG_EXTENSIONS = {".jpg", ".jpeg", ".jpe"}
RAW_EXTENSIONS = {".3fr", ".arw", ".cr2", ".cr3", ".dng", ".erf", ".iiq", ".kdc",
                  ".mef", ".mos", ".mrw", ".nef", ".nrw", ".orf", ".pef", ".raf",
                  ".raw", ".rw2", ".rwl", ".sr2", ".srf", ".srw", ".x3f"}
COLORS = ("#a6b2e5", "#8bc7d7", "#8fc9b0", "#e29aa8", "#d8ba9e")
TAG_PATTERN = re.compile(r"[\w-]+", re.UNICODE)


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: JSONの {exc.lineno} 行 {exc.colno} 列を確認してください: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path}: JSONの最上位には {{...}} 形式のオブジェクトを指定してください")
    return value


def configured_path(config_file, config, key):
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"設定 {key} にパスを指定してください: {config_file}")
    expanded = os.path.expandvars(value)
    if os.name != "nt" and re.match(r"^[A-Za-z]:", expanded):
        raise ValueError(f"設定 {key} はWindows用のパスです。利用できるパスに変更してください: {config_file}")
    return (config_file.parent / Path(expanded).expanduser()).resolve()


def validate_tag(tag):
    if not isinstance(tag, str) or not TAG_PATTERN.fullmatch(tag):
        raise ValueError(f"タグには文字・数字・ハイフン・アンダースコアを使ってください: {tag!r}")
    return tag


def reference(value, base, output, mode, allow_missing=False):
    if not value:
        return None
    if not isinstance(value, str):
        raise ValueError("画像と出典には文字列のURLまたはパスを指定してください")
    if value.startswith(("https://", "http://")):
        if mode == "local":
            raise ValueError(f"ローカル分類には画像のファイルパスを指定してください: {value}")
        if not urlsplit(value).hostname:
            raise ValueError(f"URLが正しくありません: {value}")
        return value
    if mode == "web":
        raise ValueError(f"ウェブ収集には公開ページまたは画像のHTTP(S) URLを指定してください: {value}")
    if "://" in value:
        raise ValueError(f"対応していないURLです: {value}")
    path = (base / value).resolve(strict=not allow_missing)
    if not allow_missing and not path.is_file():
        raise ValueError(f"ファイルを指定してください: {path}")
    try:
        relative = os.path.relpath(path, output.parent)
    except ValueError:  # Windows cannot relativize paths on different drives.
        return path.as_uri()
    return quote(Path(relative).as_posix(), safe="/")


FOCAL_LENGTH_STEPS = (8, 10, 12, 14, 15, 16, 18, 20, 24, 28, 35, 40, 50, 55,
                      60, 70, 85, 100, 105, 120, 135, 150, 180, 200, 250, 300,
                      400, 500, 600, 800, 1000, 1200)
APERTURE_STEPS = (1, 1.4, 2, 2.8, 4, 5.6, 8, 11, 16, 22, 32, 45, 64)
SHUTTER_STEPS = tuple(Fraction(step) for step in (
    "900", "720", "600", "480", "360", "300", "240", "180", "120", "90", "60",
    "30", "25", "20", "15", "13", "10", "8", "6", "5", "4", "3.2", "2.5", "2",
    "1.6", "1.3", "1", "0.8", "0.6", "1/2", "0.4", "1/3",
)) + tuple(Fraction(1, n) for n in (
    4, 5, 6, 8, 10, 13, 15, 20, 25, 30, 40, 50, 60, 80, 100, 125, 160, 200,
    250, 320, 400, 500, 640, 800, 1000, 1250, 1600, 2000, 2500, 3200, 4000,
    5000, 6400, 8000, 10000, 12800, 16000, 20000, 25600, 32000, 40000,
    50000, 64000, 80000,
))
ISO_STEPS = (50, 100, 200, 400, 800, 1250, 1600, 3200, 6400, 12800, 25600,
             51200, 102400, 204800, 409600, 819200, 1638400, 3280000)


def nearest_step(value, steps):
    return min(steps, key=lambda step: (abs(value - step), step)) if value is not None else None


def photo_exif(path):
    if Image is None or path.suffix.lower() == ".svg":
        return {}
    try:
        with Image.open(path) as photo:
            root = photo.getexif()
            values = {**root, **root.get_ifd(0x8769)}
    except (OSError, ValueError, SyntaxError, TypeError, Image.DecompressionBombError):
        return {}

    def model(value):
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        return " ".join(value.replace("\x00", "").split()) if isinstance(value, str) else ""

    def number(value):
        try:
            n = float(value)
            return n if math.isfinite(n) and n > 0 else None
        except (ValueError, TypeError, OverflowError, ZeroDivisionError):
            return None

    date = model(values.get(36867))  # DateTimeOriginal, not file modification time.
    try:
        date = datetime.strptime(date[:10], "%Y:%m:%d").strftime("%y-%m-%d")
    except ValueError:
        date = ""
    focal = nearest_step(number(values.get(37386)), FOCAL_LENGTH_STEPS)
    aperture_value = number(values.get(33437))
    aperture = None
    if aperture_value is not None:
        stop = min(range(len(APERTURE_STEPS)),
                   key=lambda index: abs(2 * math.log2(aperture_value) - index))
        aperture = APERTURE_STEPS[stop]
    exposure = number(values.get(33434))
    iso_value = values.get(34855) or values.get(34867)
    if isinstance(iso_value, (tuple, list)):
        iso_value = next(iter(iso_value), None)
    iso = nearest_step(number(iso_value), ISO_STEPS)
    shutter = ""
    if exposure:
        step = min(SHUTTER_STEPS, key=lambda value: abs(math.log2(exposure / value)))
        if step < 1 and step.numerator == 1:
            shutter = f"{step} s"
        else:
            shutter = f"{float(step):g} s"

    return {key: label for key, label in {
        "camera": model(values.get(272)),
        "lens": model(values.get(42036)),
        "focal-length": f"{focal:g} mm" if focal else "",
        "aperture": f"f/{aperture:g}" if aperture else "",
        "shutter": shutter,
        "iso": f"ISO {iso}" if iso else "",
        "shot-date": date,
    }.items() if label}


def exif_sort_key(tag, label):
    fields = ("camera", "lens", "focal-length", "aperture", "shutter", "iso", "shot-date")
    field = tag.removeprefix("exif-").rsplit("-", 1)[0]
    folded = label.casefold()
    if field not in fields:
        return len(fields), True, 0, folded
    rank = fields.index(field)
    try:
        if field == "shot-date":
            return rank, False, datetime.strptime(label, "%y-%m-%d").date().toordinal(), folded
        patterns = {"focal-length": r"(.+) mm", "aperture": r"f/(.+)",
                    "shutter": r"(.+) s", "iso": r"iso (.+)"}
        if field in patterns:
            match = re.fullmatch(patterns[field], folded)
            if match:
                value = Fraction(match[1])
                return rank, False, -value if field == "shutter" else value, folded
    except (ValueError, ZeroDivisionError):
        pass
    return rank, True, 0, folded


def auto_tags(path, rules):
    parts = [part.casefold() for part in (*path.parts[:-1], path.stem)]
    tokens = set(re.findall(r"[a-z0-9]+", " ".join(parts)))
    return [
        tag for tag, aliases in rules.items()
        if any(
            alias.casefold() in tokens
            if re.fullmatch(r"[a-zA-Z0-9]+", alias)
            else any(alias.casefold() in part for part in parts)
            for alias in aliases
        )
    ]


def gallery_filename(title, folder):
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(" .") or "資料ギャラリー"
    suffix = "_gallery.html"

    def fits(filename):
        return len(filename.encode("utf-8")) <= 180 and (
            os.name != "nt"
            or len(str(folder / filename).encode("utf-16-le")) // 2 <= 240
        )

    if len(stem) <= 64 and fits(stem + suffix):
        return stem + suffix

    signature = hashlib.sha256(title.encode("utf-8")).hexdigest()[:10]
    tail = "_" + signature + suffix
    prefix = stem[:55].rstrip(" ._")
    while prefix and not fits(prefix + tail):
        prefix = prefix[:-1].rstrip(" ._")
    if prefix:
        return prefix + tail
    if fits(signature + suffix):
        return signature + suffix
    raise ValueError(f"保存先のパスが長すぎます。短い場所を指定してください: {folder}")


def saved_gallery(path):
    text = path.read_text(encoding="utf-8")
    match = re.search(r'<script id="catalog" type="application/json">(.*?)</script>', text, re.DOTALL)
    if not match:
        raise ValueError(f"ギャラリー情報が見つかりません: {path}")
    catalog = json.loads(match[1])
    if not isinstance(catalog, dict):
        raise ValueError(f"ギャラリーのカタログはオブジェクトにしてください: {path}")
    saved = catalog.get("gallery", {})
    if not isinstance(saved, dict):
        raise ValueError(f"ギャラリー情報の形式が正しくありません: {path}")
    return catalog


def local_or_remote(value, directory):
    if not value or value.startswith(("https://", "http://")):
        return value
    if value.startswith("file:"):
        url = urlsplit(value)
        return url2pathname(("//" + url.netloc if url.netloc else "") + url.path)
    return str(directory / unquote(value))


def prior_rows(catalog, existing):
    groups = catalog.get("groups", {})
    for item in catalog.get("items", []):
        old_tags = item["tags"]
        group_names = [groups[tag] for tag in old_tags if tag in groups]
        entry = {"title": item["title"], "note": item.get("note", ""),
                 "groups": group_names, "tags": [tag for tag in old_tags if tag not in groups]}
        image = local_or_remote(item.get("image"), existing.parent)
        source = local_or_remote(item.get("source"), existing.parent)
        yield entry, image, source, existing.parent, True


def local_capture(row):
    _, image, source, base, _ = row
    value = image or source
    if not isinstance(value, str) or value.startswith(("https://", "http://")):
        return None, ""
    path = base / value
    return path.stem.casefold(), path.suffix.lower()


def build(args):
    config_file = args.config.resolve()
    config = read_json(config_file)
    if not isinstance(config, dict):
        raise ValueError(f"設定ファイルはJSONオブジェクトにしてください: {config_file}")
    rules_file = args.rules or configured_path(config_file, config, "tag_rules")
    rules = read_json(rules_file)
    for tag, aliases in rules.items():
        validate_tag(tag)
        if not isinstance(aliases, list) or not all(isinstance(x, str) and x for x in aliases):
            raise ValueError(f"{tag} のルールにはキーワードの配列を指定してください")
    manifest = read_json(args.manifest) if args.manifest else {}
    base = args.manifest.resolve().parent if args.manifest else Path.cwd()
    existing = args.update.resolve() if args.update else None
    prior = saved_gallery(existing) if existing else {}
    saved = prior.get("gallery", {})
    title = args.title or manifest.get("title") or saved.get("title") or (args.folder.resolve().name if args.folder else "資料ギャラリー")
    preferred = saved.get("preferredOutput")
    if not args.output and preferred and (not isinstance(preferred, str) or not Path(preferred).is_absolute()
                      or Path(preferred).suffix.lower() != ".html"):
        raise ValueError("HTMLに記録された保存先を使用できません。--output を指定してください")
    if args.output:
        requested = args.output.resolve()
        output = requested / gallery_filename(title, requested) if requested.is_dir() else requested
        if not requested.is_dir() and requested.suffix.lower() != ".html":
            raise ValueError(f"--output には既存フォルダーかHTMLファイルのパスを指定してください: {requested}")
    elif existing:
        output = Path(preferred).resolve() if preferred else existing
    else:
        output_root = configured_path(config_file, config, "output_root")
        output = output_root / gallery_filename(title, output_root)
    if not output.parent.is_dir():
        raise ValueError(f"保存先フォルダーが見つかりません。既存フォルダーを指定してください: {output.parent}")
    overwrite = args.force or output == existing
    if output.exists() and not overwrite:
        raise ValueError(f"出力先は既にあります。更新する場合は --force を付けてください: {output}")
    rows = list(prior_rows(prior, existing)) if args.append else []
    entries = manifest.get("items", [])
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise ValueError(f"{args.manifest}: items には資料オブジェクトの配列を指定してください")
    for entry in entries:
        if not isinstance(entry.get("title"), str) or not entry["title"].strip():
            raise ValueError(f"{args.manifest}: 各資料に空欄ではない title を指定してください")
        image = entry.get("image")
        source = entry.get("source") or image
        if not source:
            raise ValueError("各資料には image または source を指定してください")
        rows.append((entry, image, source, base, False))

    if args.folder:
        folder = args.folder.resolve()
        if not folder.is_dir():
            raise ValueError(f"画像フォルダーが見つかりません: {folder}")
        for path in sorted(folder.rglob("*"), key=lambda item: (item.as_posix().casefold(), item.as_posix())):
            if not path.is_file() or path.suffix.lower() not in (EXTENSIONS | JPEG_EXTENSIONS | RAW_EXTENSIONS):
                continue
            relative = path.relative_to(folder)
            group = relative.parts[0] if len(relative.parts) > 1 else "直下の画像"
            entry = {"title": path.stem, "group": group, "tags": auto_tags(relative, rules)}
            raw = path.suffix.lower() in RAW_EXTENSIONS
            if raw:
                entry["tags"].append("raw")
                entry["note"] = "RAW写真。出典リンクから元ファイルを開けます。"
            rows.append((entry, None if raw else str(path), str(path), folder, False))

    captures = [local_capture(row) for row in rows]
    jpeg_keys = {key for key, suffix in captures if suffix in JPEG_EXTENSIONS}
    rows = [row for row, (key, suffix) in zip(rows, captures)
            if suffix not in RAW_EXTENSIONS or key not in jpeg_keys]

    groups, group_ids, items, seen = {}, {}, [], {}
    exif_labels = {}
    for entry, image, source, entry_base, retained in rows:
        mode = "mixed" if retained else args.mode
        image_url = reference(image, entry_base, output, mode, retained)
        source_url = reference(source, entry_base, output, mode, retained)
        key = image_url or source_url
        if image and not image.startswith(("https://", "http://")):
            local_path = entry_base / image
            if local_path.is_file():
                with local_path.open("rb") as stream:
                    checksum = hashlib.sha256()
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        checksum.update(block)
                    key = checksum.hexdigest()
        names = entry.get("groups") if retained else [entry.get("group") or "未分類"]
        group_tags = []
        for name in names or ["未分類"]:
            if not isinstance(name, str):
                raise ValueError(f"{args.manifest}: group には文字列を指定してください")
            if name not in group_ids:
                group_ids[name] = f"group-{len(group_ids) + 1:03}"
            group = group_ids[name]
            groups[group] = name
            group_tags.append(group)
        tags = entry.get("tags", [])
        if not isinstance(tags, list):
            raise ValueError(f"{args.manifest}: tags にはタグの配列を指定してください")
        tags = list(dict.fromkeys(map(validate_tag, tags)))
        if any(re.fullmatch(r"group-\d+", tag) for tag in tags):
            raise ValueError("group-001 形式のタグは自動生成用です")
        if image and not retained and not image.startswith(("https://", "http://")):
            metadata = photo_exif(entry_base / image)
            if metadata and "photo" not in tags:
                tags.append("photo")
            for field, label in metadata.items():
                token = hashlib.sha256(label.encode("utf-8")).hexdigest()[:16]
                tag = f"exif-{field}-{token}"
                tags.append(tag)
                exif_labels[tag] = label
            tags = list(dict.fromkeys(tags))
        if key in seen:
            item = seen[key]
            item["tags"] = list(dict.fromkeys([*item["tags"], *tags, *group_tags]))
            continue
        item = {"id": f"item-{len(items) + 1}", "group": group_tags[0],
                "title": entry["title"], "note": entry.get("note", ""),
                "tags": [*tags, *group_tags], "image": image_url, "source": source_url}
        items.append(item)
        seen[key] = item

    if not items:
        raise ValueError("対象の画像・資料がありません")
    labels = manifest.get("tag_labels", {})
    if not isinstance(labels, dict) or not all(isinstance(label, str) for label in labels.values()):
        raise ValueError(f"{args.manifest}: tag_labels はタグから表示名へのオブジェクトを指定してください")
    if args.append:
        labels = {**{tag: info[0] for tag, info in prior.get("topics", {}).items()}, **labels}
    japanese_text = re.compile(r"[ぁ-ゖァ-ヺ一-龯]")
    tag_language = manifest.get("tag_language", (prior.get("tagLanguage") if args.append else None) or config.get("tag_language", "auto"))
    if tag_language == "auto":
        tag_language = "ja" if japanese_text.search(title) else "en"
    if tag_language not in ("ja", "en"):
        raise ValueError("tag_language は auto、ja、en のいずれか を指定してください")
    if tag_language == "ja":
        japanese_aliases = {
            tag: next((alias for alias in aliases if japanese_text.search(alias)), tag)
            for tag, aliases in rules.items()
        }
        labels = {**japanese_aliases, **labels}
    labels.setdefault("raw", "RAW写真" if tag_language == "ja" else "RAW photo")
    labels.update(exif_labels)
    present = dict.fromkeys(
        tag for item in items for tag in item["tags"] if tag not in groups
    )
    rule_tags = [tag for tag in rules if tag in present and not tag.startswith("exif-")]
    extra_tags = [tag for tag in present if tag not in rules and not tag.startswith("exif-")]
    exif_tags = [tag for tag in present if tag.startswith("exif-")]
    exif_tags.sort(key=lambda tag: exif_sort_key(tag, labels.get(tag, tag)))
    topic_order = [*rule_tags, *extra_tags, *exif_tags]
    topics = {tag: [labels.get(tag, tag), COLORS[index % len(COLORS)]]
              for index, tag in enumerate(topic_order)}
    gallery = {"title": title}
    if args.output or preferred:
        gallery["preferredOutput"] = str(output)
    catalog = json.dumps({"groups": groups, "items": items, "topics": topics,
                          "tagLanguage": tag_language, "topicOrder": topic_order, "gallery": gallery}, ensure_ascii=False)
    catalog = catalog.replace("<", r"\u003c")
    template = (HERE / "gallery.template.html").read_text(encoding="utf-8")
    values = {"@@TITLE@@": html.escape(title), "@@CATALOG@@": catalog}
    page = re.sub(r"@@(?:TITLE|CATALOG)@@", lambda match: values[match[0]], template)
    with output.open("w" if overwrite else "x", encoding="utf-8") as stream:
        stream.write(page)
    print(f"{len(items)} 件 / {len(groups)} グループ / {len(rows) - len(items)} 件の重複を省略")
    print(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("web", "local", "mixed"), default="mixed",
                        help="参照先をウェブ、ローカル、または混在に限定")
    parser.add_argument("--folder", type=Path, help="画像フォルダー。サブフォルダーも対象")
    parser.add_argument("--manifest", type=Path, help="手動で指定した資料のJSON")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="利用する設定JSON")
    parser.add_argument("--rules", type=Path, help="自動タグのルールJSON。設定より優先")
    parser.add_argument("--output", type=Path, help="既存フォルダーまたはHTMLファイルのパス。省略時は output_root")
    parser.add_argument("--update", type=Path, help="同じお題の既存HTML。記録済みの保存先を再利用")
    parser.add_argument("--append", action="store_true", help="--update の既存資料を保持して追加")
    parser.add_argument("--title")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.append and not args.update:
        parser.error("--append には --update を指定してください")
    if not args.folder and not args.manifest:
        parser.error("--folder または --manifest を指定してください")
    if args.mode == "web" and (not args.manifest or args.folder):
        parser.error("--mode web には --manifest を指定してください")
    try:
        build(args)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        parser.exit(2, f"{exc}\n")


if __name__ == "__main__":
    main()
