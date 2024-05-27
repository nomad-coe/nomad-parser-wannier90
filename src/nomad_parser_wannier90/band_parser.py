#
# Copyright The NOMAD Authors.
#
# This file is part of NOMAD.
# See https://nomad-lab.eu for further info.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import numpy as np
from typing import Optional
from structlog.stdlib import BoundLogger

from nomad.units import ureg
from nomad.parsing.file_parser import DataTextParser

from nomad_simulations.numerical_settings import KLinePath as KLinePathSettings
from nomad_simulations.properties import ElectronicBandStructure
from nomad_simulations.variables import KLinePath


class Wannier90BandParser:
    def __init__(self, band_file: str = ''):
        if not band_file:
            raise ValueError('Band structure `*band.dat` file not found.')
        self.band_parser = DataTextParser(mainfile=band_file)

    def parse_k_line_path_settings(self) -> Optional[KLinePathSettings]:
        return None

    def parse_band_structure(
        self, k_line_path: Optional[KLinePath], logger: BoundLogger
    ) -> Optional[ElectronicBandStructure]:
        if k_line_path is None:
            logger.info('`KLinePath` settings not found.')
            return None
