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

import os
import shutil
from pathlib import Path

current_dir = Path(__file__).resolve().parents[2]
tmp_dir = current_dir / "tmp"


def pytest_sessionfinish() -> None:
    """Cleanup tmp folder after all tests, including xdist workers."""
    # Get worker_id - it's None or not set when on master node
    """
    worker_id = os.environ.get("PYTEST_XDIST_WORKER")
    if worker_id is None:
        # This is the master node or not using xdist
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
            print(f"Tmp folder cleaned up at {tmp_dir}")
    """