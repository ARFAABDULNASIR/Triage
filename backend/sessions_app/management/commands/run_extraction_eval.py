"""Run extraction quality eval against golden transcripts."""

from pathlib import Path

import yaml
from django.conf import settings
from django.core.management.base import BaseCommand

from evals.metrics import grounding_rate, precision_recall
from integrations.extraction_pipeline import process_extraction_results
from sessions_app.heuristic_extraction import heuristic_extract


class Command(BaseCommand):
    help = "Evaluate extraction quality on golden transcript fixtures"

    def add_arguments(self, parser):
        parser.add_argument(
            "--use-llm",
            "--use-gemini",
            dest="use_llm",
            action="store_true",
            help="Call the LLM provider chain (Groq/Gemini) instead of the heuristic extractor",
        )

    def handle(self, *args, **options):
        fixtures_path = Path(settings.BASE_DIR) / "evals" / "fixtures" / "golden.yaml"
        samples_dir = Path(settings.BASE_DIR).parent / "samples"
        cases = yaml.safe_load(fixtures_path.read_text(encoding="utf-8"))

        totals = {"precision": [], "recall": [], "f1": [], "grounding": []}
        failures = []

        for case in cases:
            transcript_path = samples_dir / case["transcript_file"]
            transcript = transcript_path.read_text(encoding="utf-8")

            if options["use_llm"]:
                from integrations.llm_extraction import extract_tickets

                result = extract_tickets(transcript)
                items = result.items
            else:
                items, _ = process_extraction_results(heuristic_extract(transcript), transcript)

            predicted = [i["title"] for i in items]
            expected = case.get("expected_titles", [])
            metrics = precision_recall(predicted, expected)
            ground = grounding_rate(items, transcript)

            ok_count = case["min_tickets"] <= len(items) <= case.get("max_tickets", 99)
            ok_recall = metrics["recall"] >= 0.5

            status = "PASS" if ok_count and ok_recall else "FAIL"
            if status == "FAIL":
                failures.append(case["id"])

            self.stdout.write(f"\n[{status}] {case['id']}: {case['description']}")
            self.stdout.write(f"  Tickets: {len(items)} (expected {case['min_tickets']}-{case.get('max_tickets', '?')})")
            self.stdout.write(f"  Precision: {metrics['precision']}, Recall: {metrics['recall']}, F1: {metrics['f1']}")
            self.stdout.write(f"  Grounding rate: {ground}")
            for t in predicted:
                self.stdout.write(f"    - {t}")

            totals["precision"].append(metrics["precision"])
            totals["recall"].append(metrics["recall"])
            totals["f1"].append(metrics["f1"])
            totals["grounding"].append(ground)

        n = len(cases)
        self.stdout.write(self.style.SUCCESS(f"\n=== Summary ({n} cases) ==="))
        self.stdout.write(f"Avg precision: {sum(totals['precision'])/n:.3f}")
        self.stdout.write(f"Avg recall: {sum(totals['recall'])/n:.3f}")
        self.stdout.write(f"Avg F1: {sum(totals['f1'])/n:.3f}")
        self.stdout.write(f"Avg grounding: {sum(totals['grounding'])/n:.3f}")

        if failures:
            self.stdout.write(self.style.ERROR(f"Failed cases: {', '.join(failures)}"))
            raise SystemExit(1)
