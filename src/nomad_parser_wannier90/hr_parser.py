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
from typing import List, Optional, Tuple
from structlog.stdlib import BoundLogger

from nomad.parsing.file_parser import TextParser, Quantity

from nomad_simulations.properties import HoppingMatrix, CrystalFieldSplitting
from nomad_simulations.variables import WignerSeitz


class HrParser(TextParser):
    def __init__(self):
        super().__init__(None)

    def init_quantities(self):
        self._quantities = [
            Quantity('degeneracy_factors', r'\s*written on[\s\w]*:\d*:\d*\s*([\d\s]+)'),
            Quantity('hoppings', rf'\s*([-\d\s.]+)', repeats=False),
        ]


class Wannier90HrParser:
    def __init__(self):
        self.hr_parser = HrParser()

    def parse_hoppings(self, hr_file: Optional[str], logger: BoundLogger):
        if not hr_file:
            logger.warning('Hopping `*hr.data` file not found.')
            return None
        self.hr_parser.mainfile = hr_file

        # Parsing the degeneracy factors and values of the `HoppingMatrix`
        hopping_matrix = HoppingMatrix()
        sec_hopping_matrix.n_orbitals = (
            self.archive.run[-1].method[-1].tb.wannier.n_projected_orbitals
        )
        deg_factors = self.hr_parser.get('degeneracy_factors', [])
        if deg_factors is not None:
            sec_hopping_matrix.n_wigner_seitz_points = deg_factors[1]
            sec_hopping_matrix.degeneracy_factors = deg_factors[2:]
            full_hoppings = self.hr_parser.get('hoppings', [])
            try:
                sec_hopping_matrix.value = np.reshape(
                    full_hoppings,
                    (
                        sec_hopping_matrix.n_wigner_seitz_points,
                        sec_hopping_matrix.n_orbitals * sec_hopping_matrix.n_orbitals,
                        7,
                    ),
                )
            except Exception:
                self.logger.warning(
                    'Could not parse the hopping matrix values. Please, revise your output files.'
                )
        try:
            sec_scc_energy = Energy()
            sec_scc.energy = sec_scc_energy
            # Setting Fermi level to the first orbital onsite energy
            n_wigner_seitz_points_half = int(
                0.5 * sec_hopping_matrix.n_wigner_seitz_points
            )
            energy_fermi = (
                sec_hopping_matrix.value[n_wigner_seitz_points_half][0][5] * ureg.eV
            )
            sec_scc_energy.fermi = energy_fermi
            sec_scc_energy.highest_occupied = energy_fermi
        except Exception:
            return
