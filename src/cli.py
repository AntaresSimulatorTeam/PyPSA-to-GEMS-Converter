# Copyright (c) 2026, RTE (https://www.rte-france.com)
#
# See AUTHORS.txt
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# SPDX-License-Identifier: MPL-2.0
#
# This file is part of the Antares project.
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from pypsa import Network

from src.pypsa_converter import CONVERTER_LOGGER_NAME, PyPSAStudyConverter


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pypsa-to-gems",
        description="Convert a PyPSA network file into a GEMS study directory.",
    )
    parser.add_argument(
        "network",
        type=Path,
        help="Path to the PyPSA network (e.g. NetCDF .nc export).",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="Directory where the GEMS study will be written (created if missing).",
    )
    parser.add_argument(
        "--series-format",
        default=".tsv",
        choices=[".csv", ".tsv", "csv", "tsv"],
        help="File extension/format for exported time series (default: .tsv).",
    )
    parser.add_argument(
        "--solver",
        default="highs",
        help="Solver name written to GEMS modeler parameters (default: highs).",
    )
    return parser


def run(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not logging.root.handlers:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    log = logging.getLogger(CONVERTER_LOGGER_NAME)

    network_path = args.network.expanduser().resolve()
    if not network_path.is_file():
        log.error(f"Network path is not a file: {network_path}")
        return 1
    if network_path.suffix != ".nc":
        log.error(f"Network path is not a NetCDF file: {network_path}")
        return 1

    study_dir = args.output.expanduser().resolve()

    try:
        network = Network(str(network_path))
    except Exception:
        log.exception("Failed to load PyPSA network")
        return 1

    try:
        PyPSAStudyConverter(
            pypsa_network=network,
            study_dir=study_dir,
            series_file_format=args.series_format,
            solver_name=args.solver,
        ).to_gems_study()
    except Exception:
        log.exception("Conversion failed")
        return 1

    log.info(f"Wrote GEMS study to {study_dir}")
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
