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

import os
import logging
import numpy as np
from typing import List, Optional
from structlog.stdlib import BoundLogger

from nomad.units import ureg
from nomad.datamodel import EntryArchive
from nomad.parsing.file_parser import TextParser, Quantity, DataTextParser
from simulationworkflowschema import SinglePoint
from runschema.run import Run, Program
from runschema.calculation import (
    Calculation,
    Dos,
    DosValues,
    BandStructure,
    BandEnergies,
    Energy,
    HoppingMatrix,
)
from runschema.method import Method, AtomParameters, KMesh, Wannier, TB
from runschema.system import System, Atoms, AtomsGroup

# New schema
from nomad_simulations import Simulation, Program as BaseProgram
from nomad_simulations.model_system import ModelSystem, AtomicCell
from nomad_simulations.atoms_state import (
    AtomsState,
    CoreHole,
)
from nomad_simulations.model_method import (
    ModelMethod,
    Wannier as ModelWannier,
    KMesh as ModelKMesh,
)

from nomad_simulations.outputs import Outputs
from nomad_simulations.variables import Temperature, Energy2
from nomad_simulations.properties import (
    ElectronicBandGap,
    ElectronicDensityOfStates,
    FermiLevel,
)

from .utils import get_files
from .win_parser import WInParser, Wannier90WInParser


re_n = r'[\n\r]'


def test(template, atom_indices: list[int], **kwargs):
    simulation = Simulation()
    template.m_add_sub_section(EntryArchive.data, simulation)
    simulation.program = Program(name='VASP', version='4.6.35')
    model_system = ModelSystem()
    simulation.model_system.append(model_system)
    atomic_cell = AtomicCell(
        lattice_vectors=[
            [5.76372622e-10, 0.0, 0.0],
            [0.0, 5.76372622e-10, 0.0],
            [0.0, 0.0, 4.0755698899999997e-10],
        ],
        positions=[
            [2.88186311e-10, 0.0, 2.0377849449999999e-10],
            [0.0, 2.88186311e-10, 2.0377849449999999e-10],
            [0.0, 0.0, 0.0],
            [2.88186311e-10, 2.88186311e-10, 0.0],
        ],
        periodic_boundary_conditions=[True, True, True],
    )
    model_system.cell.append(atomic_cell)
    atoms_state = [
        AtomsState(chemical_symbol='Br'),
        AtomsState(chemical_symbol='K'),
        AtomsState(chemical_symbol='Si'),
        AtomsState(chemical_symbol='Si'),
    ]
    atomic_cell.atoms_state = atoms_state
    elements = [atom.chemical_symbol for atom in model_system.cell[0].atoms_state]

    # set atom_parameters and core_holes
    list_terms = {'j_quantum_number', 'mj_quantum_number'}
    for active_index, atom_index in enumerate(atom_indices):
        atom_state = atomic_cell.atoms_state[atom_index]
        core_hole = CoreHole()
        for k, v in kwargs.items():
            try:
                if k not in list_terms and isinstance(
                    v, list
                ):  # this supports lists of several quantities for multiple core-holes
                    if len(v):
                        setattr(core_hole, k, v[active_index])
                    else:
                        setattr(core_hole, k, [])
                else:
                    setattr(core_hole, k, v)
            except AttributeError:
                pass
        atom_state.core_hole = core_hole
        model_system2 = ModelSystem(
            type='active_atom',
            branch_label=elements[atom_index],
            atom_indices=[atom_index],
        )
        model_system.model_system.append(model_system2)
    return template


class WOutParser(TextParser):
    def __init__(self):
        super().__init__(None)

    def init_quantities(self):
        kmesh_quantities = [
            Quantity('n_points', r'Total points[\s=]*(\d+)', dtype=int, repeats=False),
            Quantity(
                'grid', r'Grid size *\= *(\d+) *x *(\d+) *x *(\d+)', repeats=False
            ),
            Quantity('k_points', r'\|[\s\d]*(-*\d.[^\|]+)', repeats=True, dtype=float),
        ]

        disentangle_quantities = [
            Quantity(
                'outer',
                r'\|\s*Outer:\s*([-\d.]+)\s*\w*\s*([-\d.]+)\s*\((?P<__unit>\w+)\)',
                dtype=float,
                repeats=False,
            ),
            Quantity(
                'inner',
                r'\|\s*Inner:\s*([-\d.]+)\s*\w*\s*([-\d.]+)\s*\((?P<__unit>\w+)\)',
                dtype=float,
                repeats=False,
            ),
        ]

        structure_quantities = [
            Quantity('labels', r'\|\s*([A-Z][a-z]*)', repeats=True),
            Quantity(
                'positions',
                rf'\|\s*([\-\d\.]+)\s*([\-\d\.]+)\s*([\-\d\.]+)',
                repeats=True,
                dtype=float,
            ),
        ]

        self._quantities = [
            # Program quantities
            Quantity(
                Program.version, r'\s*\|\s*Release\:\s*([\d\.]+)\s*', repeats=False
            ),
            # System quantities
            Quantity('lattice_vectors', r'\s*a_\d\s*([\d\-\s\.]+)', repeats=True),
            Quantity(
                'reciprocal_lattice_vectors', r'\s*b_\d\s*([\d\-\s\.]+)', repeats=True
            ),
            Quantity(
                'structure',
                rf'(\s*Fractional Coordinate[\s\S]+?)(?:{re_n}\s*(PROJECTIONS|K-POINT GRID))',
                repeats=False,
                sub_parser=TextParser(quantities=structure_quantities),
            ),
            # Method quantities
            Quantity(
                'k_mesh',
                rf'\s*(K-POINT GRID[\s\S]+?)(?:-\s*MAIN)',
                repeats=False,
                sub_parser=TextParser(quantities=kmesh_quantities),
            ),
            Quantity(
                'Nwannier',
                r'\|\s*Number of Wannier Functions\s*\:\s*(\d+)',
                repeats=False,
            ),
            Quantity(
                'Nband',
                r'\|\s*Number of input Bloch states\s*\:\s*(\d+)',
                repeats=False,
            ),
            Quantity(
                'Niter', r'\|\s*Total number of iterations\s*\:\s*(\d+)', repeats=False
            ),
            Quantity(
                'conv_tol',
                r'\|\s*Convergence tolerence\s*\:\s*([\d.eE-]+)',
                repeats=False,
            ),
            Quantity(
                'energy_windows',
                rf'(\|\s*Energy\s*Windows\s*\|[\s\S]+?)(?:Number of target bands to extract:)',
                repeats=False,
                sub_parser=TextParser(quantities=disentangle_quantities),
            ),
            # Band related quantities
            Quantity(
                'n_k_segments',
                r'\|\s*Number of K-path sections\s*\:\s*(\d+)',
                repeats=False,
            ),
            Quantity(
                'div_first_k_segment',
                r'\|\s*Divisions along first K-path section\s*\:\s*(\d+)',
                repeats=False,
            ),
            Quantity(
                'band_segments_points',
                r'\|\s*From\:\s*\w+([\d\s\-\.]+)To\:\s*\w+([\d\s\-\.]+)',
                repeats=True,
            ),
        ]


class HrParser(TextParser):
    def __init__(self):
        super().__init__(None)

    def init_quantities(self):
        self._quantities = [
            Quantity('degeneracy_factors', r'\s*written on[\s\w]*:\d*:\d*\s*([\d\s]+)'),
            Quantity('hoppings', rf'\s*([-\d\s.]+)', repeats=False),
        ]


class Wannier90ParserData:
    level = 1

    def __init__(self):
        self.wout_parser = WOutParser()
        self.band_dat_parser = DataTextParser()
        self.dos_dat_parser = DataTextParser()
        self.hr_parser = HrParser()

        self._input_projection_mapping = {
            'Nwannier': 'n_orbitals',
            'Nband': 'n_bloch_bands',
            # "conv_tol": "convergence_tolerance_max_localization",
        }

        # TODO move to normalization or utils in nomad?

    def parse_atoms_state(self, labels: List[str]) -> List[AtomsState]:
        """
        Parse the `AtomsState` from the labels by storing them as the `chemical_symbols`.

        Args:
            labels (List[str]): List of chemical element labels.

        Returns:
            (List[AtomsState]): List of `AtomsState` sections.
        """
        atoms_state = []
        for label in labels:
            atoms_state.append(AtomsState(chemical_symbol=label))
        return atoms_state

    def parse_atomic_cell(self) -> AtomicCell:
        """
        Parse the `AtomicCell` from the `lattice_vectors` and `structure` regex quantities in `WOutParser`.

        Returns:
            (AtomicCell): The parsed `AtomicCell` section.
        """
        atomic_cell = AtomicCell()

        # Parsing `lattice_vectors`
        if self.wout_parser.get('lattice_vectors', []):
            lattice_vectors = np.vstack(
                self.wout_parser.get('lattice_vectors', [])[-3:]
            )
            atomic_cell.lattice_vectors = lattice_vectors * ureg.angstrom
        # and `periodic_boundary_conditions`
        pbc = (
            [True, True, True] if lattice_vectors is not None else [False, False, False]
        )
        atomic_cell.periodic_boundary_conditions = pbc

        # Parsing `atoms_state` from `structure`
        labels = self.wout_parser.get('structure', {}).get('labels')
        atoms_state = self.parse_atoms_state(labels)
        atomic_cell.atoms_state = atoms_state
        # and parsing `positions`
        if self.wout_parser.get('structure', {}).get('positions') is not None:
            atomic_cell.positions = (
                self.wout_parser.get('structure', {}).get('positions') * ureg.angstrom
            )
        return atomic_cell

    def parse_model_system(self, logger: BoundLogger) -> Optional[ModelSystem]:
        """
        Parse the `ModelSystem` with the `AtomicCell` information. If the `structure` is not recognized in `WOutParser`, then return `None`.

        Returns:
            (Optional[ModelSystem]): The parsed `ModelSystem` section.
        """
        model_system = ModelSystem()
        model_system.is_representative = True

        # If the `structure` is not parsed, return None
        if self.wout_parser.get('structure') is None:
            logger.error('Error parsing the structure from .wout')
            return None

        atomic_cell = self.parse_atomic_cell()
        model_system.cell.append(atomic_cell)
        return model_system

    def parse_wannier(self) -> ModelWannier:
        """
        Parse the `ModelWannier` section from the `WOutParser` quantities.

        Returns:
            (ModelWannier): The parsed `ModelWannier` section.
        """
        model_wannier = ModelWannier()
        for key in self._input_projection_mapping.keys():
            setattr(
                model_wannier,
                self._input_projection_mapping[key],
                self.wout_parser.get(key),
            )
        if self.wout_parser.get('Niter'):
            model_wannier.is_maximally_localized = self.wout_parser.get('Niter', 0) > 1
        model_wannier.energy_window_outer = self.wout_parser.get(
            'energy_windows', {}
        ).get('outer')
        model_wannier.energy_window_inner = self.wout_parser.get(
            'energy_windows', {}
        ).get('inner')
        return model_wannier

    def parse_k_mesh(self) -> Optional[ModelKMesh]:
        """
        Parse the `ModelKMesh` section from the `WOutParser` quantities.

        Returns:
            (Optional[ModelKMesh]): The parsed `ModelKMesh` section.
        """
        sec_k_mesh = None
        k_mesh = self.wout_parser.get('k_mesh')
        if k_mesh:
            sec_k_mesh = ModelKMesh()
            sec_k_mesh.n_points = k_mesh.get('n_points')
            sec_k_mesh.grid = k_mesh.get('grid', [])
            if k_mesh.get('k_points') is not None:
                sec_k_mesh.points = np.complex128(k_mesh.k_points[::2])
        return sec_k_mesh

    def parse_model_method(self) -> ModelMethod:
        """
        Parse the `ModelWannier(ModelMethod)` section from the `WOutParser` quantities.

        Returns:
            (ModelMethod): The parsed `ModelWannier(ModelMethod)` section.
        """
        # `ModelMethod` section
        model_wannier = self.parse_wannier()

        # `NumericalSettings` sections
        k_mesh = self.parse_k_mesh()
        if k_mesh is not None:
            model_wannier.numerical_settings.append(k_mesh)

        return model_wannier

    def parse_fermi_level(self, output):
        fermi_level = FermiLevel(variables=[])
        fermi_level.value = 0.5 * ureg.eV
        output.fermi_level.append(fermi_level)
        # try:
        # Setting Fermi level to the first orbital onsite energy
        # n_wigner_seitz_points_half = int(
        #     0.5 * sec_hopping_matrix.n_wigner_seitz_points
        # )
        # energy_fermi = (
        #     sec_hopping_matrix.value[n_wigner_seitz_points_half][0][5] * ureg.eV
        # )
        #     fermi_level.value = energy_fermi
        #     output.fermi_level.append(fermi_level)
        # except Exception:
        #     return

    def parse_dos(self, output):
        dos_files = get_files('*dos.dat', self.filepath, self.mainfile)
        if not dos_files:
            return
        if len(dos_files) > 1:
            self.logger.warning('Multiple dos data files found.')
        # Parsing only first *dos.dat file
        self.dos_dat_parser.mainfile = dos_files[0]

        # TODO add spin polarized case
        data = np.transpose(self.dos_dat_parser.data)
        sec_dos = ElectronicDensityOfStates()
        energies = Energy2(grid_points=data[0] * ureg.eV)
        sec_dos.variables = [energies]
        sec_dos.value = data[1] / ureg.eV
        output.electronic_dos.append(sec_dos)

    def parse_output(self, simulation):
        output = Outputs(model_system_ref=simulation.model_system[-1])
        simulation.outputs.append(output)

        # Scalar band gap
        bg = ElectronicBandGap(type='direct')
        bg.variables = []
        bg.value = 1.0 * ureg.eV
        output.electronic_band_gap.append(bg)

        # T-dependent band gap
        bg = ElectronicBandGap(type='direct')
        bg.variables = [Temperature(grid_points=[1, 2, 3] * ureg.kelvin)]
        value = [1.0, 1.1, 1.2] * ureg.eV
        bg.value = value
        output.electronic_band_gap.append(bg)

        # Fermi level and DOS
        self.parse_fermi_level(output)
        self.parse_dos(output)

    def init_parser(self):
        self.wout_parser.mainfile = self.filepath
        self.wout_parser.logger = self.logger
        self.hr_parser.logger = self.logger

    def parse(self, filepath: str, archive: EntryArchive, logger: BoundLogger):
        self.filepath = filepath
        self.archive = archive
        self.maindir = os.path.dirname(self.filepath)
        self.mainfile = os.path.basename(self.filepath)

        self.init_parser()

        # Adding Simulation to data
        simulation = Simulation()
        simulation.program = BaseProgram(
            name='Wannier90',
            version=self.wout_parser.get('version', ''),
            link='https://wannier.org/',
        )
        archive.m_add_sub_section(EntryArchive.data, simulation)

        # `ModelSystem` parsing
        model_system = self.parse_model_system(logger)
        if model_system is not None:
            simulation.model_system.append(model_system)

            # Child `ModelSystem` and `OrbitalsState` parsing
            win_files = get_files('*.win', self.filepath, '*.wout')
            win_parser = Wannier90WInParser()
            child_model_systems = win_parser.parse_child_model_systems(
                win_files, model_system, logger
            )
            model_system.model_system = child_model_systems

        # `ModelWannier(ModelMethod)` parsing
        model_method = self.parse_model_method()
        simulation.model_method.append(model_method)

        # `Outputs` parsing
        self.parse_output(simulation)


class Wannier90Parser:
    level = 1

    def __init__(self):
        self.wout_parser = WOutParser()
        self.win_parser = WInParser()
        self.band_dat_parser = DataTextParser()
        self.dos_dat_parser = DataTextParser()
        self.hr_parser = HrParser()

        self._input_projection_mapping = {
            'Nwannier': 'n_projected_orbitals',
            'Nband': 'n_bands',
            'conv_tol': 'convergence_tolerance_max_localization',
        }

        self._input_projection_mapping2 = {
            'Nwannier': 'n_orbitals',
            'Nband': 'n_bloch_bands',
            'conv_tol': 'convergence_tolerance_max_localization',
        }

        self._input_projection_units = {'Ang': 'angstrom', 'Bohr': 'bohr'}

        # Angular momentum [l, mr] following Wannier90 tables 3.1 and 3.2
        # TODO move to normalization or utils in nomad?
        self._angular_momentum_orbital_map = {
            (0, 1): 's',
            (1, 1): 'px',
            (1, 2): 'py',
            (1, 3): 'pz',
            (2, 1): 'dz2',
            (2, 2): 'dxz',
            (2, 3): 'dyz',
            (2, 4): 'dx2-y2',
            (2, 5): 'dxy',
            (3, 1): 'fz3',
            (3, 2): 'fxz2',
            (3, 3): 'fyz2',
            (3, 4): 'fz(x2-y2)',
            (3, 5): 'fxyz',
            (3, 6): 'fx(x2-3y2)',
            (3, 7): 'fy(3x2-y2)',
            (-1, 1): 'sp-1',
            (-1, 2): 'sp-2',
            (-2, 1): 'sp2-1',
            (-2, 2): 'sp2-2',
            (-2, 3): 'sp2-3',
            (-3, 1): 'sp3-1',
            (-3, 2): 'sp3-2',
            (-3, 3): 'sp3-3',
            (-3, 4): 'sp3-4',
            (-4, 1): 'sp3d-1',
            (-4, 2): 'sp3d-2',
            (-4, 3): 'sp3d-3',
            (-4, 4): 'sp3d-4',
            (-4, 5): 'sp3d-5',
            (-5, 1): 'sp3d2-1',
            (-5, 2): 'sp3d2-2',
            (-5, 3): 'sp3d2-3',
            (-5, 4): 'sp3d2-4',
            (-5, 5): 'sp3d2-5',
            (-5, 6): 'sp3d2-6',
        }

        self._dft_codes = [
            'quantumespresso',
            'abinit',
            'vasp',
            'siesta',
            'wien2k',
            'fleur',
            'openmx',
            'gpaw',
        ]

    def parse_system(self):
        sec_run = self.archive.run[-1]
        sec_system = System()
        sec_run.system.append(sec_system)

        structure = self.wout_parser.get('structure')
        if structure is None:
            self.logger.error('Error parsing the structure from .wout')
            return

        sec_atoms = Atoms()
        sec_system.atoms = sec_atoms
        if self.wout_parser.get('lattice_vectors', []):
            lattice_vectors = np.vstack(
                self.wout_parser.get('lattice_vectors', [])[-3:]
            )
            sec_atoms.lattice_vectors = lattice_vectors * ureg.angstrom
        if self.wout_parser.get('reciprocal_lattice_vectors') is not None:
            sec_atoms.lattice_vectors_reciprocal = (
                np.vstack(self.wout_parser.get('reciprocal_lattice_vectors')[-3:])
                / ureg.angstrom
            )

        pbc = (
            [True, True, True] if lattice_vectors is not None else [False, False, False]
        )
        sec_atoms.periodic = pbc
        sec_atoms.labels = structure.get('labels')
        if structure.get('positions') is not None:
            sec_atoms.positions = structure.get('positions') * ureg.angstrom

    def parse_method(self):
        sec_run = self.archive.run[-1]
        sec_method = Method()
        sec_run.method.append(sec_method)
        sec_proj = TB()
        sec_method.tb = sec_proj
        sec_wann = Wannier()
        sec_proj.wannier = sec_wann

        # k_mesh section
        kmesh = self.wout_parser.get('k_mesh')
        if kmesh:
            sec_k_mesh = KMesh()
            sec_method.k_mesh = sec_k_mesh
            sec_k_mesh.n_points = kmesh.get('n_points')
            sec_k_mesh.grid = kmesh.get('grid', [])
            if kmesh.get('k_points') is not None:
                sec_k_mesh.points = np.complex128(kmesh.k_points[::2])

        # Wannier90 section
        for key in self._input_projection_mapping.keys():
            setattr(
                sec_wann, self._input_projection_mapping[key], self.wout_parser.get(key)
            )
        if self.wout_parser.get('Niter'):
            sec_wann.is_maximally_localized = self.wout_parser.get('Niter', 0) > 1
        if self.wout_parser.get('energy_windows'):
            sec_wann.energy_window_outer = self.wout_parser.get('energy_windows').outer
            sec_wann.energy_window_inner = self.wout_parser.get('energy_windows').inner

    def parse_winput(self):
        sec_run = self.archive.run[-1]
        try:
            sec_system = sec_run.system[-1]
            sec_atoms = sec_system.atoms
            sec_method = sec_run.method[-1]
        except Exception:
            self.logger.warning(
                'Could not extract system.atoms and method sections for parsing win.'
            )
            return

        # Parsing from input
        win_files = get_files('*.win', self.filepath, self.mainfile)
        if not win_files:
            self.logger.warning('Input .win file not found.')
            return
        if len(win_files) > 1:
            self.logger.warning(
                'Multiple win files found. We will parse the first one.'
            )
        self.win_parser.mainfile = win_files[0]

        def fract_cart_sites(sec_atoms, units, val):
            for pos in sec_atoms.positions.to(units):
                if np.array_equal(val, pos.magnitude):
                    index = sec_atoms.positions.magnitude.tolist().index(
                        pos.magnitude.tolist()
                    )
                    return sec_atoms.labels[index]

        # Set units in case these are defined in .win
        projections = self.win_parser.get('projections', [])
        if projections:
            if not isinstance(projections, list):
                projections = [projections]
            if projections[0][0] in ['Bohr', 'Angstrom']:
                sec_run.x_wannier90_units = self._input_projection_units[
                    projections[0][0]
                ]
                projections.pop(0)
            else:
                sec_run.x_wannier90_units = 'angstrom'
            if projections[0][0] == 'random':
                return

        # Populating AtomsGroup for projected atoms
        sec_run.x_wannier90_n_atoms_proj = len(projections)
        for nat in range(sec_run.x_wannier90_n_atoms_proj):
            sec_atoms_group = AtomsGroup()
            sec_system.atoms_group.append(sec_atoms_group)
            sec_atoms_group.type = 'active_orbitals'
            sec_atoms_group.index = (
                0  # Always first index (projection on a projection does not exist)
            )
            sec_atoms_group.is_molecule = False

            # atom label always index=0
            try:
                atom = projections[nat][0]
                if atom.startswith('f='):  # fractional coordinates
                    val = [float(x) for x in atom.replace('f=', '').split(',')]
                    val = np.dot(val, sec_atoms.lattice_vectors.magnitude)
                    sites = fract_cart_sites(sec_atoms, sec_run.x_wannier90_units, val)
                elif atom.startswith('c='):  # cartesian coordinates
                    val = [float(x) for x in atom.replace('c=', '').split(',')]
                    sites = fract_cart_sites(sec_atoms, sec_run.x_wannier90_units, val)
                else:  # atom label directly specified
                    sites = atom
                sec_atoms_group.n_atoms = len(
                    sites
                )  # always 1 (only one atom per proj)  # TODO: either add a check or default to 1
                sec_atoms_group.label = 'projection'
                sec_atoms_group.atom_indices = np.where(
                    [x == sites for x in sec_atoms.labels]
                )[0]
            except Exception:
                self.logger.warning(
                    'Error finding the atom labels for the projection from win.'
                )

            # orbital angular momentum always index=1
            # suggestion: shift to wout for projection?
            try:
                orbitals = projections[nat][1].split(';')
                sec_atom_parameters = AtomParameters()
                sec_method.atom_parameters.append(sec_atom_parameters)
                sec_atom_parameters.n_orbitals = len(orbitals)
                angular_momentum = []
                for orb in orbitals:
                    if orb.startswith('l='):  # using angular momentum numbers
                        lmom = int(orb.split(',mr')[0].replace('l=', '').split(',')[0])
                        mrmom = int(orb.split(',mr')[-1].replace('=', '').split(',')[0])
                        if (
                            orb_ang_mom := self._angular_momentum_orbital_map.get(
                                (lmom, mrmom)
                            )
                        ):  # shouldn't a missing numerical code rather generate a warning?
                            angular_momentum.append(orb_ang_mom)
                    else:  # ang mom label directly specified
                        angular_momentum.append(orb)
                sec_atom_parameters.orbitals = np.array(angular_momentum)
            except Exception:
                self.logger.warning('Projected orbital labels not found from win.')

    def parse_hoppings(self):
        hr_files = get_files('*hr.dat', self.filepath, self.mainfile)
        if not hr_files:
            return
        self.hr_parser.mainfile = hr_files[0]

        # Assuming method.tb is parsed before
        sec_scc = self.archive.run[-1].calculation[-1]
        sec_hopping_matrix = HoppingMatrix()
        sec_scc.hopping_matrix.append(sec_hopping_matrix)
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

    def get_k_points(self):
        if self.wout_parser.get('reciprocal_lattice_vectors') is None:
            return
        reciprocal_lattice_vectors = np.vstack(
            self.wout_parser.get('reciprocal_lattice_vectors')
        )

        n_k_segments = self.wout_parser.get('n_k_segments', [])
        k_symm_points = []
        k_symm_points_cart = []
        for ns in range(n_k_segments):
            k_segments = np.split(self.wout_parser.get('band_segments_points')[ns], 2)
            [
                k_symm_points_cart.append(
                    np.dot(k_segments[i], reciprocal_lattice_vectors)
                )
                for i in range(2)
            ]
            [k_symm_points.append(k_segments[i]) for i in range(2)]

        n_k_segments_points_1 = self.wout_parser.get('div_first_k_segment')
        delta_k = (
            np.linalg.norm(k_symm_points_cart[1] - k_symm_points_cart[0])
            / n_k_segments_points_1
        )
        band_segments_points = []
        for ns in range(n_k_segments):
            n_k_segments_points = round(
                np.linalg.norm(
                    k_symm_points_cart[2 * ns + 1] - k_symm_points_cart[2 * ns]
                )
                / delta_k
            )
            band_segments_points.append(n_k_segments_points)

        kpoints = []
        for n in range(len(band_segments_points)):
            kpoints_segment = [
                k_symm_points[2 * n]
                + i
                * (k_symm_points[2 * n + 1] - k_symm_points[2 * n])
                / band_segments_points[n]
                for i in range(band_segments_points[n])
            ]
            kpoints.append(kpoints_segment)

        # TODO check having to add manually last point (?)
        band_segments_points[-1] = band_segments_points[-1] + 1
        kpoints[-1].append(k_symm_points[-1])
        n_kpoints = sum(band_segments_points)

        return (n_kpoints, band_segments_points, kpoints)

    def parse_bandstructure(self):
        sec_scc = self.archive.run[-1].calculation[-1]

        try:
            energy_fermi = sec_scc.energy.fermi
        except Exception:
            self.logger.warning(
                'Error setting the Fermi level: not found from hoppings. Setting it to 0 eV'
            )
            energy_fermi = 0.0 * ureg.eV
        energy_fermi_eV = energy_fermi.to('electron_volt').magnitude

        band_files = get_files('*band.dat', self.filepath, self.mainfile)
        if not band_files:
            return
        if len(band_files) > 1:
            self.logger.warning('Multiple bandstructure data files found.')
        # Parsing only first *_band.dat file
        self.band_dat_parser.mainfile = band_files[0]

        sec_k_band = BandStructure()
        sec_scc.band_structure_electronic.append(sec_k_band)
        sec_k_band.energy_fermi = energy_fermi
        try:
            sec_k_band.reciprocal_cell = (
                self.archive.run[-1].system[0].atoms.lattice_vectors_reciprocal
            )
        except Exception:
            self.logger.warning(
                'Reciprocal cell in band_structure_electronic not set up.'
            )

        if self.band_dat_parser.data is None:
            return
        data = np.transpose(self.band_dat_parser.data)

        (n_kpoints, band_segments_points, kpoints) = self.get_k_points()
        n_segments = len(band_segments_points)
        n_bands = round((len(data[0])) / n_kpoints)
        n_spin = 1

        # reshaping bands into the NOMAD band_structure style
        try:
            bands = np.transpose(np.reshape(data[1], (n_bands, n_kpoints)))
        except Exception:
            self.logger.warning(
                'Could not reshape the bands into the NOMAD dimensions.'
            )
            return
        bkp_init = 0
        for n in range(n_segments):
            sec_k_band_segment = BandEnergies()
            sec_k_band.segment.append(sec_k_band_segment)
            sec_k_band_segment.n_kpoints = band_segments_points[n]
            sec_k_band_segment.kpoints = kpoints[n]

            bkp_last = bkp_init + band_segments_points[n]
            energies = np.reshape(
                bands[bkp_init:bkp_last, :], (n_spin, band_segments_points[n], n_bands)
            )
            occs = np.reshape(
                np.array(
                    [
                        2.0 if energies[i, j, k] < energy_fermi_eV else 0.0
                        for i in range(n_spin)
                        for j in range(band_segments_points[n])
                        for k in range(n_bands)
                    ]
                ),
                (n_spin, band_segments_points[n], n_bands),
            )
            bkp_init = bkp_last
            sec_k_band_segment.energies = energies * ureg.eV
            sec_k_band_segment.occupations = occs

    def parse_dos(self):
        sec_scc = self.archive.run[-1].calculation[-1]

        try:
            energy_fermi = sec_scc.energy.fermi
        except Exception:
            self.logger.warning(
                'Error setting the Fermi level: not found from hoppings. Setting it to 0 eV'
            )
            energy_fermi = 0.0 * ureg.eV

        dos_files = get_files('*dos.dat', self.filepath, self.mainfile)
        if not dos_files:
            return
        if len(dos_files) > 1:
            self.logger.warning('Multiple dos data files found.')
        # Parsing only first *dos.dat file
        self.dos_dat_parser.mainfile = dos_files[0]

        # TODO add spin polarized case
        sec_dos = Dos()
        sec_scc.dos_electronic.append(sec_dos)
        sec_dos.energy_fermi = energy_fermi
        data = np.transpose(self.dos_dat_parser.data)
        sec_dos.n_energies = len(data[0])
        sec_dos.energies = data[0] * ureg.eV
        sec_dos_values = DosValues()
        sec_dos.total.append(sec_dos_values)
        sec_dos_values.value = data[1] / ureg.eV

    def parse_scc(self):
        sec_run = self.archive.run[-1]
        sec_scc = Calculation()
        sec_run.calculation.append(sec_scc)

        # Refs for calculation
        sec_scc.method_ref = sec_run.method[-1]
        sec_scc.system_ref = sec_run.system[-1]

        # Wannier90 hoppings section
        self.parse_hoppings()

        # Wannier band structure
        self.parse_bandstructure()

        # Wannier DOS
        self.parse_dos()

    def init_parser(self):
        self.wout_parser.mainfile = self.filepath
        self.wout_parser.logger = self.logger
        self.hr_parser.logger = self.logger

    def parse(self, filepath, archive, logger):
        self.filepath = filepath
        self.archive = archive
        self.maindir = os.path.dirname(self.filepath)
        self.mainfile = os.path.basename(self.filepath)
        self.logger = logging.getLogger(__name__) if logger is None else logger

        self.init_parser()

        sec_run = Run()
        self.archive.run.append(sec_run)

        # Program section
        sec_run.program = Program(
            name='Wannier90', version=self.wout_parser.get('version', '')
        )
        # TODO TimeRun section

        self.parse_system()

        self.parse_method()

        # Parsing AtomsGroup and AtomParameters for System and Method from the input file .win
        self.parse_winput()

        self.parse_scc()

        workflow = SinglePoint()
        self.archive.workflow2 = workflow

        # Adding `data` section
        p = Wannier90ParserData()
        p.parse(filepath, archive, logger)
