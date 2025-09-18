# smart_agent/executor/replicate_service.py
from __future__ import annotations

import os
import logging
import tempfile
from typing import Any, Dict, Optional, List, Tuple

import replicate
from replicate.exceptions import ReplicateError, ModelError

from executor.config import (
    REPLICATE_API_TOKEN,
    OPENAI_API_KEY,
    MODEL_INTERIOR_DESIGN_REF,
    MODEL_INTERIOR_DESIGN_IMAGE_PARAM,
    MODEL_INTERIOR_DESIGN_NEEDS_OPENAI_KEY,
    MODEL_FLOOR_PLAN_REF,
    MODEL_FLOOR_PLAN_IMAGE_PARAM,
    MODEL_FLOOR_PLAN_NEEDS_OPENAI_KEY,
)

LOG = logging.getLogger(__name__)

# Гарантируем токен Replicate в окружении (как раньше)
if REPLICATE_API_TOKEN and not os.getenv("REPLICATE_API_TOKEN"):
    os.environ["REPLICATE_API_TOKEN"] = REPLICATE_API_TOKEN

# Дефолты «как в старом коде»
DEFAULT_INTERIOR_DESIGN_REF = "adirik/interior-design:76604baddc85b1b4616e1c6475eca080da339c8875bd4996705440484a6eac38"
DEFAULT_FLOOR_PLAN_REF = "openai/gpt-image-1"
DEFAULT_INTERIOR_IMAGE_PARAM = "image"
DEFAULT_FLOOR_IMAGE_PARAM = "input_images"  # в старом коде именно так
DEFAULT_INTERIOR_NEEDS_OPENAI = False
DEFAULT_FLOOR_NEEDS_OPENAI = True  # gpt-image-1 требует ключ


class ReplicateService:
    """
    Инкапсуляция вызовов Replicate для:
      - редизайна/дизайна с нуля (run_design)
      - генерации планировок (run_floor_plan)

    ВАЖНО: Сигнатуры публичных методов совпадают с прежними функциями.
    """

    def __init__(self) -> None:
        self._interior_ref = MODEL_INTERIOR_DESIGN_REF or DEFAULT_INTERIOR_DESIGN_REF
        self._floor_ref = MODEL_FLOOR_PLAN_REF or DEFAULT_FLOOR_PLAN_REF

        self._interior_img_param = MODEL_INTERIOR_DESIGN_IMAGE_PARAM or DEFAULT_INTERIOR_IMAGE_PARAM
        self._floor_img_param = MODEL_FLOOR_PLAN_IMAGE_PARAM or DEFAULT_FLOOR_IMAGE_PARAM

        self._interior_needs_openai = (
            DEFAULT_INTERIOR_NEEDS_OPENAI
            if MODEL_INTERIOR_DESIGN_NEEDS_OPENAI_KEY is None
            else bool(MODEL_INTERIOR_DESIGN_NEEDS_OPENAI_KEY)
        )
        self._floor_needs_openai = (
            DEFAULT_FLOOR_NEEDS_OPENAI
            if MODEL_FLOOR_PLAN_NEEDS_OPENAI_KEY is None
            else bool(MODEL_FLOOR_PLAN_NEEDS_OPENAI_KEY)
        )
        # Подстраховка: для openai/* всегда тащим ключ и стартуем с input_images
        if self._floor_ref.startswith("openai/") or "gpt-image" in self._floor_ref:
            self._floor_needs_openai = True
            self._floor_img_param = "input_images"

    # ---------- public ----------

    def run_design(self, img_bytes: bytes, prompt: str) -> str:
        """
        Редизайн/дизайн с нуля.
        В модель уходит ФАЙЛ по параметру изображения (как раньше).
        Возвращает URL результата или бросает исключение.
        """
        tmp_path = None
        try:
            tmp_path = self._bytes_to_tempfile(img_bytes, suffix=".png")
            input_dict, fhs = self._build_input_dict(
                prompt=prompt,
                image_param=self._interior_img_param,
                img_path=tmp_path,
                needs_openai_key=self._interior_needs_openai,
                openai_api_key=OPENAI_API_KEY,
            )

            LOG.info("Replicate run (design). model=%s, keys=%s", self._interior_ref, list(input_dict.keys()))
            out = replicate.run(self._interior_ref, input=input_dict)

            url = self._extract_url(out)
            if not url:
                raise RuntimeError(f"Unexpected output for design: {out!r}")
            return url
        finally:
            # закрываем дескрипторы и чистим временный файл
            try:
                self._close_fhs(fhs if 'fhs' in locals() else [])
            except Exception:
                pass
            self._safe_unlink(tmp_path)

    def run_floor_plan(self, img_bytes: bytes, prompt: str) -> str:
        """
        Генерация планировок. Стартуем строго с 'input_images' и включённым openai_api_key
        для openai/gpt-image-1. Если модель «не видит» изображение, пробуем другие имена поля.
        Возвращает URL результата или бросает исключение.
        """
        tmp_path = None
        try:
            tmp_path = self._bytes_to_tempfile(img_bytes, suffix=".png")
            return self._run_with_image_param_fallback(
                model_ref=self._floor_ref,
                img_path=tmp_path,
                prompt=prompt,
                image_param=self._floor_img_param,  # старт: input_images
                needs_openai_key=self._floor_needs_openai,
                openai_api_key=OPENAI_API_KEY,
            )
        finally:
            self._safe_unlink(tmp_path)

    # ---------- internal ----------

    def _run_with_image_param_fallback(
        self,
        *,
        model_ref: str,
        img_path: str,
        prompt: str,
        image_param: str,
        needs_openai_key: bool,
        openai_api_key: Optional[str],
    ) -> str:
        """
        Пробуем image → input_image → input_images, если модель не видит картинку.
        Возвращает URL результата или бросает исключение.
        """
        order: List[str] = [image_param] + [p for p in ["image", "input_image", "input_images"] if p != image_param]
        last_err: Optional[Exception] = None

        for param in order:
            input_dict: Dict[str, Any] = {}
            fhs: List[Any] = []
            try:
                input_dict, fhs = self._build_input_dict(
                    prompt=prompt,
                    image_param=param,
                    img_path=img_path,
                    needs_openai_key=needs_openai_key,
                    openai_api_key=openai_api_key,
                )
                LOG.info(
                    "Replicate run (plan). try param=%s, model=%s, keys=%s",
                    param, model_ref, list(input_dict.keys())
                )
                out = replicate.run(model_ref, input=input_dict)
                url = self._extract_url(out)
                if url:
                    if param != image_param:
                        LOG.warning("Image param auto-switched: %s -> %s", image_param, param)
                    return url
                last_err = RuntimeError("No URL in output")
            except (ModelError, ReplicateError) as e:
                self._log_replicate_error(f"Replicate error with param={param}", e)
                pred = getattr(e, "prediction", None)
                metrics = getattr(pred, "metrics", {}) if pred else {}
                image_count = (metrics or {}).get("image_count")
                # Если модель явно не увидела изображение — пробуем следующий параметр
                if image_count == 0:
                    last_err = e
                    continue
                # Иная ошибка — отдаём сразу
                raise
            except Exception as e:
                last_err = e
            finally:
                # всегда закрываем открытые дескрипторы
                try:
                    self._close_fhs(fhs)
                except Exception:
                    pass

        if last_err:
            raise last_err
        raise RuntimeError("Unknown replicate fallback failure")

    @staticmethod
    def _build_input_dict(
        *,
        prompt: str,
        image_param: str,
        img_path: str,
        needs_openai_key: bool,
        openai_api_key: Optional[str],
    ) -> Tuple[Dict[str, Any], List[Any]]:
        """
        Формирует input для replicate.run.
        Картинка — файловый дескриптор; для input_images — список из одного файла.
        Возвращает (input_dict, [fh1, fh2...]) — чтобы корректно закрывать файлы.
        """
        if not img_path or not os.path.exists(img_path) or os.path.getsize(img_path) == 0:
            raise RuntimeError(f"Image file not found or empty: {img_path}")

        fh = open(img_path, "rb")  # ВАЖНО: передаём именно file-like
        input_dict: Dict[str, Any] = {"prompt": prompt}
        fhs: List[Any] = [fh]

        if needs_openai_key and openai_api_key:
            input_dict["openai_api_key"] = openai_api_key

        if image_param == "input_images":
            input_dict["input_images"] = [fh]
        else:
            input_dict[image_param] = fh

        return input_dict, fhs

    @staticmethod
    def _close_fhs(fhs: List[Any]) -> None:
        for fh in fhs or []:
            try:
                fh.close()
            except Exception:
                pass

    @staticmethod
    def _extract_url(output: Any) -> Optional[str]:
        """
        Универсальный парсер результата replicate.run (как в старом коде).
        """
        try:
            # вариант: список объектов/строк
            if isinstance(output, list):
                for item in output:
                    if isinstance(item, str) and item.startswith("http"):
                        return item
                for item in output:
                    url = getattr(item, "url", None)
                    if isinstance(url, str) and url.startswith("http"):
                        return url
            # вариант: один объект с .url
            url = getattr(output, "url", None)
            if isinstance(url, str) and url.startswith("http"):
                return url
            # вариант: dict
            if isinstance(output, dict):
                if "url" in output and isinstance(output["url"], str) and output["url"].startswith("http"):
                    return output["url"]
                if "output" in output:
                    res = output["output"]
                    if isinstance(res, str) and res.startswith("http"):
                        return res
                    if isinstance(res, list):
                        for item in res:
                            if isinstance(item, str) and item.startswith("http"):
                                return item
            return None
        except Exception:
            return None

    @staticmethod
    def _bytes_to_tempfile(data: bytes, suffix: str = ".png") -> str:
        if not data or len(data) < 64:
            raise RuntimeError("Empty or too small image bytes")
        fd, path = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return path

    @staticmethod
    def _safe_unlink(path: Optional[str]) -> None:
        if not path:
            return
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except Exception as e:
            LOG.warning("Temp image not removed: %s (%s)", path, e)

    @staticmethod
    def _log_replicate_error(ctx: str, exc: Exception) -> None:
        try:
            pred = getattr(exc, "prediction", None)
            metrics = getattr(pred, "metrics", {}) if pred else {}
            LOG.warning("%s: %s | metrics=%s", ctx, exc, metrics)
        except Exception:
            LOG.warning("%s: %s", ctx, exc)

    # Совместимость: публичный хелпер (если нужно снаружи)
    log_replicate_error = _log_replicate_error


# ---------- module-level API (совместимость) ----------

_RS = ReplicateService()

def run_design(img_bytes: bytes, prompt: str) -> str:
    return _RS.run_design(img_bytes, prompt)

def run_floor_plan(img_bytes: bytes, prompt: str) -> str:
    return _RS.run_floor_plan(img_bytes, prompt)
