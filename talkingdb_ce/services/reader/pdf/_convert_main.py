import json
import math
import os
import sys


def _atomic_write_json(path: str, data: dict) -> None:
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp_path, path)


def _completed_batch_indexes(checkpoint_dir: str) -> list[int]:
    indexes = []
    for name in os.listdir(checkpoint_dir):
        if name.startswith("batch_") and name.endswith(".json"):
            try:
                indexes.append(int(name[len("batch_"):-len(".json")]))
            except ValueError:
                continue
    return sorted(indexes)


def _batch_path(checkpoint_dir: str, batch_idx: int) -> str:
    return os.path.join(checkpoint_dir, f"batch_{batch_idx:05d}.json")


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        sys.stderr.write(
            "usage: _convert_main <pdf_path> <docx_path> <pages_path> <checkpoint_dir>\n"
        )
        return 2

    pdf_path, docx_path, pages_path, checkpoint_dir = argv

    try:
        from docx import Document
        from pdf2docx import Converter
        import fitz
    except Exception as exc:
        sys.stderr.write(f"ImportError: {exc}\n")
        return 3

    page_numbers: list[int] = []

    try:
        os.makedirs(checkpoint_dir, exist_ok=True)
        manifest_path = os.path.join(checkpoint_dir, "manifest.json")
        progress_path = os.path.join(checkpoint_dir, "progress.json")
        batch_size = max(1, int(os.getenv("CE_PDF_CONVERT_BATCH_PAGES", "50")))

        if os.path.exists(manifest_path):
            with open(manifest_path) as fh:
                manifest = json.load(fh)
        else:
            fitz_doc = fitz.open(pdf_path)
            try:
                total_pages = fitz_doc.page_count
            finally:
                fitz_doc.close()
            manifest = {
                "total_pages": total_pages,
                "batch_size": batch_size,
                "total_batches": max(1, math.ceil(total_pages / batch_size)),
            }
            _atomic_write_json(manifest_path, manifest)

        total_pages = manifest["total_pages"]
        batch_size = manifest["batch_size"]
        total_batches = manifest["total_batches"]

        done_batches = _completed_batch_indexes(checkpoint_dir)
        next_batch = (done_batches[-1] + 1) if done_batches else 0

        _atomic_write_json(
            progress_path,
            {
                "completed_batches": len(done_batches),
                "total_batches": total_batches,
                "completed_pages": min(next_batch * batch_size, total_pages),
                "total_pages": total_pages,
            },
        )

        for batch_idx in range(next_batch, total_batches):
            start = batch_idx * batch_size
            end = min(start + batch_size, total_pages)

            batch_path = _batch_path(checkpoint_dir, batch_idx)
            batch_tmp_path = f"{batch_path}.tmp"
            cv = Converter(pdf_path)
            try:
                settings = cv.default_settings
                cv.parse(start=start, end=end, **settings)
                cv.serialize(batch_tmp_path)
                os.replace(batch_tmp_path, batch_path)
            finally:
                cv.close()

            _atomic_write_json(
                progress_path,
                {
                    "completed_batches": batch_idx + 1,
                    "total_batches": total_batches,
                    "completed_pages": end,
                    "total_pages": total_pages,
                },
            )

        acc = Converter(pdf_path)
        try:
            settings = acc.default_settings
            for batch_idx in range(total_batches):
                acc.deserialize(_batch_path(checkpoint_dir, batch_idx))

            parsed_pages = [p for p in acc.pages if p.finalized]
            if not parsed_pages:
                raise ValueError("No parsed pages produced by pdf2docx")

            docx_file = Document()

            for page in parsed_pages:
                try:
                    page.make_docx(docx_file)
                except Exception as exc:
                    if not settings.get("ignore_page_error", True):
                        raise
                    sys.stderr.write(f"WARNING: skipped page {page.id + 1}: {exc}\n")
                    continue

                # pdf2docx starts a new docx section per page (Page.make_docx),
                # so this order matches the section order in the saved docx.
                page_numbers.append(page.id + 1)

            docx_file.save(docx_path)
        finally:
            acc.close()
    except BaseException as exc:
        sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
        return 1

    with open(pages_path, "w") as fh:
        json.dump(page_numbers, fh)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
