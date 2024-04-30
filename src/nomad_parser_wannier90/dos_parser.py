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

from nomad_simulations.properties import ElectronicDensityOfStates
from nomad_simulations.variables import Energy2


class Wannier90DosParser:
    def __init__(self):
        self.dos_parser = DataTextParser()

    def parse_dos(
        self, dos_file: Optional[str], logger: BoundLogger
    ) -> Optional[ElectronicDensityOfStates]:
        if not dos_file:
            logger.warning('DOS `*dos.dat` file not found.')
            return None
        self.dos_parser.mainfile = dos_file

        # TODO add spin polarized case
        data = np.transpose(self.dos_parser.data)
        sec_dos = ElectronicDensityOfStates()
        energies = Energy2(grid_points=data[0] * ureg.eV)
        sec_dos.variables.append(energies)
        sec_dos.value = data[1] / ureg.eV
        return sec_dos
