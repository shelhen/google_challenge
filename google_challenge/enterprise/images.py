"""验证码图片保存、图块拼接和人工答案规范化。"""

from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image, ImageDraw

from .. import config
from .protocol import EnterpriseUserVerifyError


class ChallengeImages:
    """负责图片保存、编号网格绘制和人工答案处理。"""

    @staticmethod
    def _image_extension(content: bytes) -> str:
        """根据图片文件头识别常用格式。"""
        if content.startswith(b"\xff\xd8\xff"):
            return ".jpg"
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png"
        if content.startswith((b"GIF87a", b"GIF89a")):
            return ".gif"
        if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
            return ".webp"
        return ".bin"

    def _save_challenge(self, challenge: Dict[str, Any], run_dir: Path) -> Dict[str, Any]:
        """保存当前图片正文、编号网格和挑战元数据。"""
        round_number = int(challenge.get("round") or 0)
        round_dir = run_dir / f"round_{round_number:03d}"
        round_dir.mkdir(parents=True, exist_ok=True)
        source_data: Dict[str, bytes] = {}
        source_paths: Dict[str, str] = {}
        errors: List[str] = []
        embedded_bodies = {
            str(url): str(value)
            for url, value in (challenge.get("imageBodies") or {}).items()
            if value
        }
        for index, url in enumerate(challenge.get("imageURLs") or [], 1):
            raw_path = round_dir / f"source_{index:02d}.bin"
            try:
                encoded = embedded_bodies.get(str(url))
                if encoded:
                    data = base64.b64decode(encoded)
                    raw_path.write_bytes(data)
                else:
                    raise EnterpriseUserVerifyError(
                        "mini-browser 尚未提供该图片资源；不另行下载，请检查图片加载请求"
                    )
                extension = self._image_extension(data)
                final_path = raw_path.with_suffix(extension)
                raw_path.replace(final_path)
                source_data[str(url)] = data
                source_paths[str(url)] = str(final_path)
            except Exception as exc:
                errors.append(f"{url}: {exc!r}")
        grid_path = round_dir / "challenge_grid.png"
        try:
            self._compose_grid(challenge, source_data, grid_path)
            rendered_path: Optional[str] = str(grid_path)
        except Exception as exc:
            rendered_path = None
            errors.append(f"compose: {exc!r}")
        output = dict(challenge)
        output.pop("fingerprint", None)
        output.pop("imageBodies", None)
        output["image_body_lengths"] = {
            url: len(base64.b64decode(value)) for url, value in embedded_bodies.items()
        }
        output["image_path"] = rendered_path
        output["source_paths"] = source_paths
        output["download_errors"] = errors
        (round_dir / "challenge.json").write_text(
            json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return output

    @staticmethod
    def _crop_tile(
        image: Image.Image, tile: Dict[str, Any], *, rows: int, columns: int, shared_source: bool
    ) -> Image.Image:
        """按照背景偏移从共享图片中裁剪单个图块。"""
        left_percent = abs(float(tile.get("leftPercent") or 0))
        top_percent = abs(float(tile.get("topPercent") or 0))
        use_composite = shared_source or left_percent > 0 or top_percent > 0
        if not use_composite:
            return image.copy()
        tile_width = image.width / max(1, columns)
        tile_height = image.height / max(1, rows)
        left = int(round(left_percent / 100.0 * tile_width))
        top = int(round(top_percent / 100.0 * tile_height))
        right = min(image.width, int(round(left + tile_width)))
        bottom = min(image.height, int(round(top + tile_height)))
        if right <= left or bottom <= top:
            return image.copy()
        return image.crop((left, top, right, bottom))

    def _compose_grid(
        self, challenge: Dict[str, Any], source_data: Dict[str, bytes], destination: Path
    ) -> None:
        """将图片图块绘制为带编号的本地网格。"""
        rows = max(1, int(challenge.get("rows") or 1))
        columns = max(1, int(challenge.get("columns") or 1))
        tiles = list(challenge.get("tiles") or [])
        counts: Dict[str, int] = {}
        images: Dict[str, Image.Image] = {}
        for tile in tiles:
            url = str(tile.get("src") or "")
            counts[url] = counts.get(url, 0) + 1
        for url, data in source_data.items():
            images[url] = Image.open(io.BytesIO(data)).convert("RGB")
        cell_size = config.IMAGE_CELL_SIZE
        canvas = Image.new("RGB", (columns * cell_size, rows * cell_size), "white")
        draw = ImageDraw.Draw(canvas)
        for offset, tile in enumerate(tiles):
            row = offset // columns
            column = offset % columns
            url = str(tile.get("src") or "")
            source = images.get(url)
            if source is None:
                crop = Image.new("RGB", (cell_size, cell_size), "#dddddd")
            else:
                crop = self._crop_tile(
                    source, tile, rows=rows, columns=columns, shared_source=counts.get(url, 0) > 1
                )
                crop.thumbnail((cell_size, cell_size), Image.Resampling.LANCZOS)
                background = Image.new("RGB", (cell_size, cell_size), "white")
                left = (cell_size - crop.width) // 2
                top = (cell_size - crop.height) // 2
                background.paste(crop, (left, top))
                crop = background
            x = column * cell_size
            y = row * cell_size
            canvas.paste(crop, (x, y))
            border = "#0b8043" if tile.get("selected") else "#ffffff"
            draw.rectangle(
                (x, y, x + cell_size - 1, y + cell_size - 1),
                outline=border,
                width=5 if tile.get("selected") else 2,
            )
            label = str(tile.get("index") or offset + 1)
            draw.rectangle((x + 4, y + 4, x + 38, y + 32), fill="#111111")
            draw.text((x + 13, y + 9), label, fill="#ffffff")
        canvas.save(destination)

    @staticmethod
    def normalize_answer(answer: Any) -> Dict[str, Any]:
        """将序号列表或中文、英文命令转换为标准动作。"""
        if isinstance(answer, dict):
            action = str(answer.get("action") or "").lower()
            output = dict(answer)
            output["action"] = action
            return output
        if isinstance(answer, (list, tuple, set)):
            return {"action": "select", "indices": [int(value) for value in answer]}
        text = str(answer or "").strip().lower()
        if text in {"q", "quit", "exit", "退出"}:
            return {"action": "quit"}
        if text in {"r", "refresh", "reload", "换一张"}:
            return {"action": "refresh"}
        if text in {"v", "verify", "submit", "验证", "提交"}:
            return {"action": "verify"}
        if text in {"s", "skip", "next", "跳过", "下一步"}:
            return {"action": "skip"}
        parts = [part for part in re.split("[\\s,，;；]+", text) if part]
        if parts and all((part.isdigit() for part in parts)):
            return {"action": "select", "indices": [int(part) for part in parts]}
        raise ValueError("输入格式：序号如 1,4,7；v=验证；s=跳过；r=刷新；q=退出")

    def console_answer_provider(
        self, challenge: Dict[str, Any], *, read_line=None
    ) -> Dict[str, Any]:
        """显示图片路径和操作提示，并读取人工答案。"""
        print(
            f"\n[Enterprise challenge] round={challenge.get('round')} grid={challenge.get('rows')}x{challenge.get('columns')}",
            flush=True,
        )
        if challenge.get("prompt"):
            print(str(challenge["prompt"]), flush=True)
        if challenge.get("errors"):
            print("提示：" + " | ".join(challenge["errors"]), flush=True)
        print("图片：" + str(challenge.get("image_path") or "生成失败"), flush=True)
        print(
            f"当前按钮：{challenge.get('verifyButtonText') or '未知'}（{('禁用' if challenge.get('verifyButtonDisabled') else '可用')}）；已发 userverify：{challenge.get('userverifyCount', 0)} 次。",
            flush=True,
        )
        if challenge.get("controlsOnly"):
            print("页面未就绪，当前仅可尝试 r=刷新，或 q=退出。", flush=True)
        else:
            print(
                "输入序号仅选择图块；观察替换后的新图再继续输入。 v=点击当前按钮（下一个不等于最终验证）；s=同样点击当前按钮，仅显示跳过时才是跳过；r=刷新，q=退出。",
                flush=True,
            )
        while True:
            try:
                return self.normalize_answer((read_line or input)("序号/命令> "))
            except ValueError as exc:
                print(str(exc), flush=True)
