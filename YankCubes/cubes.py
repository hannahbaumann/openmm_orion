# (C) 2018 OpenEye Scientific Software Inc. All rights reserved.
#
# TERMS FOR USE OF SAMPLE CODE The software below ("Sample Code") is
# provided to current licensees or subscribers of OpenEye products or
# SaaS offerings (each a "Customer").
# Customer is hereby permitted to use, copy, and modify the Sample Code,
# subject to these terms. OpenEye claims no rights to Customer's
# modifications. Modification of Sample Code is at Customer's sole and
# exclusive risk. Sample Code may require Customer to have a then
# current license or subscription to the applicable OpenEye offering.
# THE SAMPLE CODE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED.  OPENEYE DISCLAIMS ALL WARRANTIES, INCLUDING, BUT
# NOT LIMITED TO, WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
# PARTICULAR PURPOSE AND NONINFRINGEMENT. In no event shall OpenEye be
# liable for any damages or liability in connection with the Sample Code
# or its use.

import traceback
from floe.api import (ParallelMixin,
                      parameter)

from cuberecord import OERecordComputeCube

from cuberecord.ports import RecordInputPort

from datarecord import (Types,
                        Meta,
                        OEField,
                        OERecord,
                        OEFieldMeta)

import MDCubes.OpenMMCubes.utils as omm_utils

from openeye import oechem

from simtk.openmm import (app,
                          unit,
                          XmlSerializer)

from tempfile import TemporaryDirectory

import tarfile

import os

import itertools

from YankCubes.yank_templates import (yank_solvation_template,
                                      yank_binding_template,
                                      )

from YankCubes import utils as yankutils

from yank.analyze import ExperimentAnalyzer

from Standards import (MDStageNames,
                       Fields,
                       MDRecords)

import copy

import textwrap

import subprocess

from oeommtools import utils as oeommutils

from orionclient.session import in_orion, OrionSession
from orionclient.types import File
from os import environ


class YankSolvationFECube(ParallelMixin, OERecordComputeCube):
    version = "0.0.0"
    title = "YankSolvationFECube"
    description = """
    Compute the hydration free energy of a small molecule with YANK.

    This cube uses the YANK alchemical free energy code to compute the
    transfer free energy of one or more small molecules from gas phase
    to the selected solvent.

    See http://getyank.org for more information about YANK.
    """
    classification = [["Alchemical free energy calculations"]]
    tags = [tag for lists in classification for tag in lists]

    # Override defaults for some parameters
    parameter_overrides = {
        "gpu_count": {"default": 1},
        "memory_mb": {"default": 6000},
        "instance_tags": {"default": "cuda8"},
        "spot_policy": {"default": "Allowed"},
        "prefetch_count": {"default": 1},  # 1 molecule at a time
        "item_count": {"default": 1}  # 1 molecule at a time
    }

    temperature = parameter.DecimalParameter(
        'temperature',
        default=300.0,
        help_text="Temperature (Kelvin)")

    pressure = parameter.DecimalParameter(
        'pressure',
        default=1.0,
        help_text="Pressure (atm)")

    minimize = parameter.BooleanParameter(
        'minimize',
        default=True,
        help_text="Minimize input system")

    iterations = parameter.IntegerParameter(
        'iterations',
        default=1000,
        help_text="Number of iterations")

    nsteps_per_iteration = parameter.IntegerParameter(
        'nsteps_per_iteration',
        default=500,
        help_text="Number of steps per iteration")

    nonbondedCutoff = parameter.DecimalParameter(
        'nonbondedCutoff',
        default=10.0,
        help_text="The non-bonded cutoff in angstroms")

    ligand_res_name = parameter.StringParameter(
        'ligand_res_name',
        required=True,
        default='LIG',
        help_text='Ligand residue name')

    verbose = parameter.BooleanParameter(
        'verbose',
        default=False,
        help_text="Print verbose YANK logging output")

    rerun = parameter.BooleanParameter(
        'rerun',
        default=False,
        help_text="Start Yank Restart procedure")

    analyze = parameter.BooleanParameter(
        'analyze',
        default=False,
        help_text="Start Yank Analysis on the collected results")

    hmr = parameter.BooleanParameter(
        'hmr',
        default=False,
        description='Hydrogen Mass Repartitioning')

    def begin(self):
        self.opt = vars(self.args)
        self.opt['Logger'] = self.log

    def process(self, record, port):

        try:

            self.log.warn(">>>>>>> {} verbose {}".format(self.title, self.opt['verbose']))
            self.log.warn(">>>>>>> {} rerun {}".format(self.title, self.opt['rerun']))
            self.log.warn(">>>>>>> {} analyze {}".format(self.title, self.opt['analyze']))
            self.log.warn(">>>>>>> {} iterations {}".format(self.title, self.opt['iterations']))
            self.log.warn(">>>>>>> {} pressure {}".format(self.title, self.opt['pressure']))
            self.log.warn(">>>>>>> {} temperature {}".format(self.title, self.opt['temperature']))
            self.log.warn(">>>>>>> {} minimize {}".format(self.title, self.opt['minimize']))
            self.log.warn(">>>>>>> {} min_parallel {}".format(self.title, self.opt['min_parallel']))
            self.log.warn(">>>>>>> {} max_parallel {}".format(self.title, self.opt['max_parallel']))

            # The copy of the dictionary option as local variable
            # is necessary to avoid filename collisions due to
            # the parallel cube processes
            opt = dict(self.opt)

            # Logger string
            str_logger = '-' * 32 + ' YANK SOLV CUBE PARAMETERS ' + '-' * 32
            for k, v in sorted(self.parameters().items()):
                tmp_default = copy.deepcopy(v)

                if v.default is None:
                    tmp_default.default = 'None'
                elif isinstance(v, parameter.BooleanParameter):
                    if v.default:
                        tmp_default.default = 'True'
                    else:
                        tmp_default.default = 'False'
                else:
                    tmp_description = textwrap.fill(" ".join(v.description.split()),
                                                    subsequent_indent=' ' * 39, width=80)
                    str_logger += "\n{:<25} = {:<10} {}".format(k,
                                                                getattr(self.args, tmp_default.name),
                                                                tmp_description)

            if not record.has_value(Fields.primary_molecule):
                raise ValueError("Missing the Primary Molecule field")

            system = record.get_value(Fields.primary_molecule)

            if not record.has_value(Fields.title):
                opt['Logger'].warn("Missing record Title field")
                system_title = system.GetTitle()[0:12]
            else:
                system_title = record.get_value(Fields.title)

            if not record.has_value(Fields.id):
                raise ValueError("Missing the Primary Molecule field")

            prot_split, lig_split, water, excipients = oeommutils.split(system, ligand_res_name=opt['ligand_res_name'])

            fchg_lig = 0
            for at in lig_split.GetAtoms():
                fchg_lig += at.GetFormalCharge()

            if fchg_lig != 0:
                alchemical_pme_treatment = 'exact'
            else:
                alchemical_pme_treatment = 'direct-space'

            opt['system_title'] = system_title

            opt['system_id'] = record.get_value(Fields.id)

            # Update cube simulation parameters with the eventually molecule SD tags
            new_args = {dp.GetTag(): dp.GetValue() for dp in oechem.OEGetSDDataPairs(system) if dp.GetTag() in
                        ["temperature"]}
            if new_args:
                for k in new_args:
                    try:
                        new_args[k] = float(new_args[k])
                    except:
                        pass
                self.log.info("Updating parameters for molecule: {}\n{}".format(system.GetTitle(), new_args))
                opt.update(new_args)

            if not record.has_value(Fields.md_stages):
                raise ValueError("The System does not seem to be parametrized by the Force Field")

            # Extract the MDStageRecord list
            md_stages = record.get_value(Fields.md_stages)

            # Extract the most recent MDStageRecord
            md_stage_record = md_stages[-1]

            # Extract the MDSystemRecord
            md_system_record = md_stage_record.get_value(Fields.md_system)

            # Extract from the MDSystemRecord the the Parmed structure
            parmed_structure = md_system_record.get_value(Fields.structure)

            # Extract the MD data
            mdData = omm_utils.MDData(parmed_structure)
            solvated_structure = mdData.structure

            # Extract the ligand parmed structure
            solute_structure = solvated_structure.split()[0][0]
            solute_structure.box = None

            solvent_res_names = set()
            for res in solvated_structure.residues:
                solvent_res_names.add(res.name)
            solvent_res_names.remove(solute_structure.residues[0].name)

            solvent_res_names = list(solvent_res_names)

            for i in range(0, len(solvent_res_names)):
                if '+' in solvent_res_names[i]:
                    solvent_res_names[i] = "'"+solvent_res_names[i]+"'"
                if '-' in solvent_res_names[i]:
                    solvent_res_names[i] = "'"+solvent_res_names[i]+"'"

            solvent_str_names = ' '.join(solvent_res_names)
            
            # Write out all the required files and set-run the Yank experiment
            with TemporaryDirectory() as output_directory:

                opt['Logger'].info("Output Directory {}".format(output_directory))

                solvated_structure_fn = os.path.join(output_directory, "solvated.pdb")
                solute_structure_fn = os.path.join(output_directory, "solute.pdb")

                solvated_omm_sys_serialized_fn = os.path.join(output_directory, "solvated.xml")
                solute_omm_sys_serialized_fn = os.path.join(output_directory, "solute.xml")

                if opt['rerun']:
                    yank_files = md_stage_record.get_value(Fields.trajectory)
                    filename = omm_utils.download(yank_files)

                    with tarfile.open(filename) as tar:
                        tar.extractall(path=output_directory)
                        # os.remove(filename)

                    # Disable minimization if restart is enabled
                    opt['minimize'] = False

                else:
                    solvated_structure.save(solvated_structure_fn, overwrite=True)
                    solute_structure.save(solute_structure_fn, overwrite=True)

                    # Create the solvated and vacuum system
                    solvated_omm_sys = solvated_structure.createSystem(nonbondedMethod=app.PME,
                                                                       nonbondedCutoff=opt['nonbondedCutoff'] * unit.angstroms,
                                                                       constraints=app.HBonds,
                                                                       removeCMMotion=False)

                    solute_omm_sys = solute_structure.createSystem(nonbondedMethod=app.NoCutoff,
                                                                   constraints=app.HBonds,
                                                                   removeCMMotion=False)

                    solvated_omm_sys_serialized = XmlSerializer.serialize(solvated_omm_sys)
                    with open(solvated_omm_sys_serialized_fn, 'w') as solvated_f:
                        solvated_f.write(solvated_omm_sys_serialized)

                    solute_omm_sys_serialized = XmlSerializer.serialize(solute_omm_sys)
                    with open(solute_omm_sys_serialized_fn, 'w') as solute_f:
                        solute_f.write(solute_omm_sys_serialized)

                yank_template = yank_solvation_template.format(
                                                 verbose='yes' if opt['verbose'] else 'no',
                                                 minimize='yes' if opt['minimize'] else 'no',
                                                 output_directory=output_directory,
                                                 timestep=4.0 if opt['hmr'] else 2.0,
                                                 nsteps_per_iteration=opt['nsteps_per_iteration'],
                                                 number_iterations=opt['iterations'],
                                                 temperature=opt['temperature'],
                                                 pressure=opt['pressure'],
                                                 resume_sim='yes' if opt['rerun'] else 'no',
                                                 resume_setup='yes' if opt['rerun'] else 'no',
                                                 hydrogen_mass=4.0 if opt['hmr'] else 1.0,
                                                 alchemical_pme_treatment=alchemical_pme_treatment,
                                                 solvated_pdb_fn=solvated_structure_fn,
                                                 solvated_xml_fn=solvated_omm_sys_serialized_fn,
                                                 solute_pdb_fn=solute_structure_fn,
                                                 solute_xml_fn=solute_omm_sys_serialized_fn,
                                                 solvent_dsl=solvent_str_names)

                opt['yank_template'] = yank_template

                yankutils.run_yank(opt)

                if opt['analyze']:

                    exp_dir = os.path.join(output_directory, "experiments")

                    experiment_to_analyze = ExperimentAnalyzer(exp_dir)
                    analysis = experiment_to_analyze.auto_analyze()

                    # Calculate solvation free energy and its error
                    DeltaG_solvation = analysis['free_energy']['free_energy_diff_unit'].\
                                         in_units_of(unit.kilocalorie_per_mole)/unit.kilocalorie_per_mole
                    dDeltaG_solvation = analysis['free_energy']['free_energy_diff_error_unit'].\
                                          in_units_of(unit.kilocalorie_per_mole)/unit.kilocalorie_per_mole

                    # Create OE Field to save the Solvation Free Energy in kcal/mol
                    DG_Field = OEField('Solvation FE', Types.Float,
                                       meta=OEFieldMeta().set_option(Meta.Units.Energy.kCal_per_mol))
                    dG_Field = OEField('Solvation FE Error', Types.Float,
                                       meta=OEFieldMeta().set_option(Meta.Units.Energy.kCal_per_mol))

                    record.set_value(DG_Field, DeltaG_solvation)
                    record.set_value(dG_Field, dDeltaG_solvation)

                    opt_1 = '--store={}'.format(exp_dir)

                    result_fn = os.path.join(output_directory, 'results.html')
                    opt_2 = '--output={}'.format(result_fn)

                    opt_3 = '--format=html'

                    try:
                        os.environ["HDF5_USE_FILE_LOCKING"] = "FALSE"

                        subprocess.check_call(['yank', 'analyze', 'report', opt_1, opt_2, opt_3])

                        with open(result_fn, 'r') as f:
                            result_str = f.read()

                        record.set_value(Fields.yank_analysis, result_str)

                        # Upload Floe Report
                        if in_orion():
                            session = OrionSession()

                            file_upload = File.upload(session,
                                                      "{} MD Cluster Report".format(opt['system_title']),
                                                      result_fn)

                            session.tag_resource(file_upload, "floe_report")

                            job_id = environ.get('ORION_JOB_ID')

                            if job_id:
                                session.tag_resource(file_upload, "Job {}".format(job_id))

                    except subprocess.SubprocessError:
                        opt['Logger'].warn("The result file have not been generated")

                # Tar the Yank temp dir with its content:
                tar_fn = os.path.basename(output_directory) + '.tar.gz'
                with tarfile.open(tar_fn, mode='w:gz') as archive:
                    archive.add(output_directory, arcname='.', recursive=True)

                # Create Large file object if required
                lf = omm_utils.upload(tar_fn)

                str_logger += '\n' + '-' * 32 + ' SIMULATION ' + '-' * 32

                with open(os.path.join(output_directory, "experiments/experiments.log"), 'r') as flog:
                    str_logger += '\n'+flog.read()

                str_logger += ">>>>>>> {} verbose {}".format(self.title, self.opt['verbose'])
                str_logger += ">>>>>>> {} rerun {}".format(self.title, self.opt['rerun'])
                str_logger += ">>>>>>> {} analyze {}".format(self.title, self.opt['analyze'])
                str_logger += ">>>>>>> {} iterations {}".format(self.title, self.opt['iterations'])
                str_logger += ">>>>>>> {} pressure {}".format(self.title, self.opt['pressure'])
                str_logger += ">>>>>>> {} temperature {}".format(self.title, self.opt['temperature'])
                str_logger += ">>>>>>> {} minimize {}".format(self.title, self.opt['minimize'])
                str_logger += ">>>>>>> {} min_parallel {}".format(self.title, self.opt['min_parallel'])
                str_logger += ">>>>>>> {} max_parallel {}".format(self.title, self.opt['max_parallel'])

                md_stage_record = MDRecords.MDStageRecord(MDStageNames.FEC,
                                                          MDRecords.MDSystemRecord(system, mdData.structure),
                                                          log=str_logger,
                                                          trajectory=lf)

                # md_stages.append(md_stage_record)
                md_stages[-1] = md_stage_record

                record.set_value(Fields.md_stages, md_stages)

                record.set_value(Fields.primary_molecule, system)

                # Emit the ligand
                self.success.emit(record)

        except:
            # Attach an error message to the molecule that failed
            self.log.error(traceback.format_exc())
            # Return failed mol
            self.failure.emit(record)

        return


class SyncBindingFECube(OERecordComputeCube):
    version = "0.0.0"
    title = "SyncSolvationFECube"
    description = """
    This cube is used to synchronize the solvated ligands and the related
    solvated complexes
    """
    classification = [["Synchronization Cube"]]
    tags = [tag for lists in classification for tag in lists]

    solvated_ligand_in_port = RecordInputPort("solvated_ligand_in_port")

    def begin(self):
        self.opt = vars(self.args)
        self.opt['Logger'] = self.log
        self.solvated_ligand_list = []
        self.solvated_complex_list = []

    def process(self, record, port):

        try:

            if port == 'solvated_ligand_in_port':
                self.solvated_ligand_list.append(record)
            else:
                self.solvated_complex_list.append(record)

        except:
            self.log.error(traceback.format_exc())
            # Return failed mol
            self.failure.emit(record)

        return

    def end(self):
        try:

            solvated_complex_lig_list = [(i, j) for i, j in
                                         itertools.product(self.solvated_ligand_list, self.solvated_complex_list)
                                         if i.get_value(Fields.id) == j.get_value(Fields.id)]

            for pair in solvated_complex_lig_list:

                new_record = OERecord()

                self.log.info("SYNC = ({} - {} , {} - {})".format(pair[0].get_value(Fields.title),
                                                                  pair[0].get_value(Fields.id),
                                                                  pair[1].get_value(Fields.title),
                                                                  pair[1].get_value(Fields.id)))

                complex_solvated = pair[1].get_value(Fields.primary_molecule)
                new_record.set_value(Fields.primary_molecule, complex_solvated)
                new_record.set_value(Fields.id, pair[1].get_value(Fields.id))
                new_record.set_value(Fields.title, pair[1].get_value(Fields.title))

                ligand_solvated_field = OEField("ligand_solvated", Types.Record)
                complex_solvated_field = OEField("complex_solvated", Types.Record)
                new_record.set_value(ligand_solvated_field, pair[0].get_value(Fields.md_stages)[-1].get_value(Fields.md_system))
                new_record.set_value(complex_solvated_field, pair[1].get_value(Fields.md_stages)[-1].get_value(Fields.md_system))

                self.success.emit(new_record)

        except:
            self.log.error(traceback.format_exc())

        return


class YankBindingFECube(ParallelMixin, OERecordComputeCube):
    version = "0.0.0"
    title = "YankBindingFECube"
    description = """
    Compute the hydration free energy of a small molecule with YANK.

    This cube uses the YANK alchemical free energy code to compute the
    transfer free energy of one or more small molecules from gas phase
    to the selected solvent.

    See http://getyank.org for more information about YANK.
    """
    classification = [["Alchemical free energy calculations"]]
    tags = [tag for lists in classification for tag in lists]

    # Override defaults for some parameters
    parameter_overrides = {
        "gpu_count": {"default": 1},
        "memory_mb": {"default": 6000},
        "instance_tags": {"default": "cuda8"},
        "spot_policy": {"default": "Allowed"},
        "prefetch_count": {"default": 1},  # 1 molecule at a time
        "item_count": {"default": 1}  # 1 molecule at a time
    }

    temperature = parameter.DecimalParameter(
        'temperature',
        default=300.0,
        help_text="Temperature (Kelvin)")

    pressure = parameter.DecimalParameter(
        'pressure',
        default=1.0,
        help_text="Pressure (atm)")

    minimize = parameter.BooleanParameter(
        'minimize',
        default=True,
        help_text="Minimize input system")

    iterations = parameter.IntegerParameter(
        'iterations',
        default=1000,
        help_text="Number of iterations")

    nsteps_per_iteration = parameter.IntegerParameter(
        'nsteps_per_iteration',
        default=500,
        help_text="Number of steps per iteration")

    timestep = parameter.DecimalParameter(
        'timestep',
        default=2.0,
        help_text="Timestep (fs)")

    nonbondedCutoff = parameter.DecimalParameter(
        'nonbondedCutoff',
        default=10.0,
        help_text="The non-bonded cutoff in angstroms")

    ligand_resname = parameter.StringParameter(
        'ligand_resname',
        default='LIG',
        help_text='The decoupling ligand residue name')

    verbose = parameter.BooleanParameter(
        'verbose',
        default=False,
        help_text="Print verbose YANK logging output")

    rerun = parameter.BooleanParameter(
        'rerun',
        default=False,
        help_text="Start Yank Restart procedure")

    analyze = parameter.BooleanParameter(
        'analyze',
        default=False,
        help_text="Start Yank Analysis on the collected results")

    hmr = parameter.BooleanParameter(
        'hmr',
        default=False,
        description='Hydrogen Mass Repartitioning')

    sampler = parameter.StringParameter(
        'sampler',
        required=True,
        default='repex',
        choices=['repex', 'sams'],
        help_text='Yank Sampling mode: REPEX Replica Exchange and SAMS Self-Adjusted Mixture Sampling')

    restraints = parameter.StringParameter(
        'restraints',
        required=True,
        default='harmonic',
        choices=['harmonic', 'boresch'],
        help_text='Select the restraint type')

    protocol = parameter.StringParameter(
        'protocol',
        required=True,
        default='restraint_on_short',
        choices=['auto_protocol', 'restraint_on_short', 'restraint_on_long'],
        help_text='Select the protocol type')

    def begin(self):
        self.opt = vars(self.args)
        self.opt['Logger'] = self.log

    def process(self, record, port):

        try:
            opt = dict(self.opt)

            self.log.warn(">>>>>>> {} verbose {}".format(self.title, self.opt['verbose']))
            self.log.warn(">>>>>>> {} rerun {}".format(self.title, self.opt['rerun']))
            self.log.warn(">>>>>>> {} analyze {}".format(self.title, self.opt['analyze']))
            self.log.warn(">>>>>>> {} iterations {}".format(self.title, self.opt['iterations']))
            self.log.warn(">>>>>>> {} pressure {}".format(self.title, self.opt['pressure']))
            self.log.warn(">>>>>>> {} temperature {}".format(self.title, self.opt['temperature']))
            self.log.warn(">>>>>>> {} minimize {}".format(self.title, self.opt['minimize']))
            self.log.warn(">>>>>>> {} min_parallel {}".format(self.title, self.opt['min_parallel']))
            self.log.warn(">>>>>>> {} max_parallel {}".format(self.title, self.opt['max_parallel']))

            # Logger string
            str_logger = '-' * 32 + ' YANK BINDING CUBE PARAMETERS ' + '-' * 32
            for k, v in sorted(self.parameters().items()):
                tmp_default = copy.deepcopy(v)

                if v.default is None:
                    tmp_default.default = 'None'
                elif isinstance(v, parameter.BooleanParameter):
                    if v.default:
                        tmp_default.default = 'True'
                    else:
                        tmp_default.default = 'False'
                else:
                    tmp_description = textwrap.fill(" ".join(v.description.split()),
                                                    subsequent_indent=' ' * 39, width=80)
                    str_logger += "\n{:<25} = {:<10} {}".format(k,
                                                                getattr(self.args, tmp_default.name),
                                                                tmp_description)

            if not record.has_value(Fields.primary_molecule):
                raise ValueError("The Primary Molecule is missing field")

            complex = record.get_value(Fields.primary_molecule)

            # Split the complex in components
            protein_split, ligand_split, water, excipients = oeommutils.split(complex,
                                                                              ligand_res_name=self.opt['ligand_resname'])
            fchg_lig = 0
            for at in ligand_split.GetAtoms():
                fchg_lig += at.GetFormalCharge()

            if fchg_lig != 0:
                alchemical_pme_treatment = 'exact'
            else:
                alchemical_pme_treatment = 'direct-space'

            solvent = water.CreateCopy()

            if not oechem.OEAddMols(solvent, excipients):
                raise ValueError("Solvent merging failure")

            solvent_res_names = set()

            hv = oechem.OEHierView(solvent)
            for chain in hv.GetChains():
                for frag in chain.GetFragments():
                    for hres in frag.GetResidues():
                        solvent_res_names.add(hres.GetOEResidue().GetName())

            solvent_res_names = list(solvent_res_names)

            for i in range(0, len(solvent_res_names)):
                if '+' in solvent_res_names[i]:
                    solvent_res_names[i] = "'"+solvent_res_names[i]+"'"
                if '-' in solvent_res_names[i]:
                    solvent_res_names[i] = "'"+solvent_res_names[i]+"'"

            solvent_str_names = ' '.join(solvent_res_names)

            if not record.has_value(Fields.title):
                opt['Logger'].warn("Missing record Title field")
                system_title = complex.GetTitle()[0:12]
            else:
                system_title = record.get_value(Fields.title)

            if not record.has_value(Fields.id):
                raise ValueError("Missing the ID field")

            opt['system_title'] = system_title

            opt['system_id'] = record.get_value(Fields.id)

            if not opt['rerun']:
                solvated_ligand_record_field = OEField("ligand_solvated", Types.Record)

                if not record.has_value(solvated_ligand_record_field):
                    raise ValueError("The parametrized solvated ligand is missing")

                solvated_ligand_mdsystem_record = record.get_value(solvated_ligand_record_field)

                solvated_ligand_parmed_structure = solvated_ligand_mdsystem_record.get_value(Fields.structure)

                solvated_complex_record_field = OEField("complex_solvated", Types.Record)

                if not record.has_value(solvated_complex_record_field):
                    raise ValueError("The parametrized solvated complex is missing")

                solvated_complex_mdsystem_record = record.get_value(solvated_complex_record_field)

                solvated_complex_parmed_structure = solvated_complex_mdsystem_record.get_value(Fields.structure)

            else:
                # Extract the MDStageRecord list
                md_stages = record.get_value(Fields.md_stages)

                # Extract the most recent MDStageRecord
                md_stage_record = md_stages[-1]

                complex_mdsystem_record = md_stage_record.get_value(Fields.md_system)

                solvated_complex_parmed_structure = complex_mdsystem_record.get_value(Fields.structure)

            # Write out all the required files and set-run the Yank experiment
            with TemporaryDirectory() as output_directory:

                opt['Logger'].info("Output Directory {}".format(output_directory))

                solvated_complex_structure_fn = os.path.join(output_directory, "complex.pdb")
                solvated_ligand_structure_fn = os.path.join(output_directory, "solvent.pdb")
                solvated_complex_omm_serialized_fn = os.path.join(output_directory, "complex.xml")
                solvated_ligand_omm_serialized_fn = os.path.join(output_directory, "solvent.xml")

                if opt['rerun']:
                    yank_files = md_stage_record.get_value(Fields.trajectory)
                    filename = omm_utils.download(yank_files)

                    with tarfile.open(filename) as tar:
                        tar.extractall(path=output_directory)
                        # os.remove(filename)

                    # Disable minimization if restart is enabled
                    opt['minimize'] = False
                else:
                    solvated_complex_parmed_structure.save(solvated_complex_structure_fn, overwrite=True)
                    solvated_ligand_parmed_structure.save(solvated_ligand_structure_fn, overwrite=True)

                    # Create the solvated OpenMM systems
                    solvated_complex_omm_sys = solvated_complex_parmed_structure.createSystem(nonbondedMethod=app.PME,
                                                                                              ewaldErrorTolerance=1.0e-4,
                                                                                              nonbondedCutoff=opt['nonbondedCutoff'] * unit.angstroms,
                                                                                              constraints=app.HBonds,
                                                                                              removeCMMotion=False)

                    solvated_ligand_omm_sys = solvated_ligand_parmed_structure.createSystem(nonbondedMethod=app.PME,
                                                                                            ewaldErrorTolerance=1.0e-4,
                                                                                            nonbondedCutoff=opt['nonbondedCutoff'] * unit.angstroms,
                                                                                            constraints=app.HBonds,
                                                                                            removeCMMotion=False)

                    solvated_complex_omm_serialized = XmlSerializer.serialize(solvated_complex_omm_sys)

                    with open(solvated_complex_omm_serialized_fn, 'w') as solvated_complex_f:
                        solvated_complex_f.write(solvated_complex_omm_serialized)

                    solvated_ligand_omm_serialized = XmlSerializer.serialize(solvated_ligand_omm_sys)

                    with open(solvated_ligand_omm_serialized_fn, 'w') as solvated_ligand_f:
                        solvated_ligand_f.write(solvated_ligand_omm_serialized)

                yank_template = yank_binding_template.format(
                    verbose='yes' if opt['verbose'] else 'no',
                    minimize='yes' if opt['minimize'] else 'no',
                    output_directory=output_directory,
                    timestep=opt['timestep'],
                    nsteps_per_iteration=opt['nsteps_per_iteration'],
                    number_iterations=opt['iterations'],
                    temperature=opt['temperature'],
                    pressure=opt['pressure'],
                    resume_sim='yes' if opt['rerun'] else 'no',
                    resume_setup='yes' if opt['rerun'] else 'no',
                    hydrogen_mass=4.0 if opt['hmr'] else 1.0,
                    alchemical_pme_treatment=alchemical_pme_treatment,
                    complex_pdb_fn=solvated_complex_structure_fn,
                    complex_xml_fn=solvated_complex_omm_serialized_fn,
                    solvent_pdb_fn=solvated_ligand_structure_fn,
                    solvent_xml_fn=solvated_ligand_omm_serialized_fn,
                    ligand_resname=opt['ligand_resname'],
                    solvent_dsl=solvent_str_names,
                    sampler=opt['sampler'],
                    restraints=opt['restraints'],
                    protocol=opt['protocol']
                )

                opt['yank_template'] = yank_template

                yankutils.run_yank(opt)

                if opt['analyze']:
                    exp_dir = os.path.join(output_directory, "experiments")

                    experiment_to_analyze = ExperimentAnalyzer(exp_dir)
                    analysis = experiment_to_analyze.auto_analyze()

                    # Calculate binding free energy and its error in kcal/mol
                    DeltaG_binding = analysis['free_energy']['free_energy_diff_unit'].\
                                         in_units_of(unit.kilocalorie_per_mole)/unit.kilocalorie_per_mole
                    dDeltaG_binding = analysis['free_energy']['free_energy_diff_error_unit'].\
                                          in_units_of(unit.kilocalorie_per_mole)/unit.kilocalorie_per_mole

                    # Create OE Field to save the Solvation Free Energy in kcal/mol
                    DG_Field = OEField('Binding Affinity', Types.Float,
                                       meta=OEFieldMeta().set_option(Meta.Units.Energy.kCal_per_mol))
                    dG_Field = OEField('Binding Affinity Error', Types.Float,
                                       meta=OEFieldMeta().set_option(Meta.Units.Energy.kCal_per_mol))

                    record.set_value(DG_Field, DeltaG_binding)
                    record.set_value(dG_Field, dDeltaG_binding)

                    opt_1 = '--store={}'.format(exp_dir)

                    result_fn = os.path.join(output_directory, 'results.html')
                    opt_2 = '--output={}'.format(result_fn)

                    opt_3 = '--format=html'

                    try:

                        os.environ["HDF5_USE_FILE_LOCKING"] = "FALSE"

                        subprocess.check_call(['yank', 'analyze', 'report', opt_1, opt_2, opt_3])

                        with open(result_fn, 'r') as f:
                            result_str = f.read()

                        record.set_value(Fields.yank_analysis, result_str)

                        # Upload Floe Report
                        if in_orion():
                            session = OrionSession()

                            file_upload = File.upload(session,
                                                      "{} MD Cluster Report".format(opt['system_title']),
                                                      result_fn)

                            session.tag_resource(file_upload, "floe_report")

                            job_id = environ.get('ORION_JOB_ID')

                            if job_id:
                                session.tag_resource(file_upload, "Job {}".format(job_id))

                    except subprocess.SubprocessError:
                        opt['Logger'].warn("The result file have not been generated")

                # Tar the Yank temp dir with its content:
                tar_fn = os.path.basename(output_directory+"_"+opt['system_title']) + '.tar.gz'
                with tarfile.open(tar_fn, mode='w:gz') as archive:
                    archive.add(output_directory, arcname='.', recursive=True)

                # Create Large file object if required
                lf = omm_utils.upload(tar_fn)

                str_logger += '\n' + '-' * 32 + ' SIMULATION ' + '-' * 32

                with open(os.path.join(output_directory, "experiments/experiments.log"), 'r') as flog:
                    str_logger += '\n' + flog.read()

                str_logger += ">>>>>>> {} verbose {}".format(self.title, self.opt['verbose'])
                str_logger += ">>>>>>> {} rerun {}".format(self.title, self.opt['rerun'])
                str_logger += ">>>>>>> {} analyze {}".format(self.title, self.opt['analyze'])
                str_logger += ">>>>>>> {} iterations {}".format(self.title, self.opt['iterations'])
                str_logger += ">>>>>>> {} pressure {}".format(self.title, self.opt['pressure'])
                str_logger += ">>>>>>> {} temperature {}".format(self.title, self.opt['temperature'])
                str_logger += ">>>>>>> {} minimize {}".format(self.title, self.opt['minimize'])
                str_logger += ">>>>>>> {} min_parallel {}".format(self.title, self.opt['min_parallel'])
                str_logger += ">>>>>>> {} max_parallel {}".format(self.title, self.opt['max_parallel'])

                md_stage_record = MDRecords.MDStageRecord(MDStageNames.FEC,
                                                          MDRecords.MDSystemRecord(complex,
                                                                                   solvated_complex_parmed_structure),
                                                          log=str_logger,
                                                          trajectory=lf)
                if opt['rerun']:
                    # md_stages.append(md_stage_record)
                    md_stages[-1] = md_stage_record
                    record.set_value(Fields.md_stages, md_stages)
                else:
                    record = OERecord()
                    record.set_value(Fields.md_stages, [md_stage_record])
                    record.set_value(Fields.id, opt['system_id'])
                    record.set_value(Fields.title, opt['system_title'])

            record.set_value(Fields.primary_molecule, complex)

            self.success.emit(record)

        except:
            # Attach an error message to the molecule that failed
            self.log.error(traceback.format_exc())
            # Return failed mol
            self.failure.emit(record)

        return


# class TestFECube(ParallelMixin, OERecordComputeCube):
#     version = "0.0.0"
#     title = "YankSolvationFECube"
#     description = """
#     Compute the hydration free energy of a small molecule with YANK.
#
#     This cube uses the YANK alchemical free energy code to compute the
#     transfer free energy of one or more small molecules from gas phase
#     to the selected solvent.
#
#     See http://getyank.org for more information about YANK.
#     """
#     classification = [["Alchemical free energy calculations"]]
#     tags = [tag for lists in classification for tag in lists]
#
#     # Override defaults for some parameters
#     parameter_overrides = {
#         "gpu_count": {"default": 1},
#         "memory_mb": {"default": 6000},
#         "instance_tags": {"default": "cuda8"},
#         "spot_policy": {"default": "Allowed"},
#         "prefetch_count": {"default": 1},  # 1 molecule at a time
#         "item_count": {"default": 1}  # 1 molecule at a time
#     }
#
#     temperature = parameter.DecimalParameter(
#         'temperature',
#         default=300.0,
#         help_text="Temperature (Kelvin)")
#
#     pressure = parameter.DecimalParameter(
#         'pressure',
#         default=1.0,
#         help_text="Pressure (atm)")
#
#     minimize = parameter.BooleanParameter(
#         'minimize',
#         default=True,
#         help_text="Minimize input system")
#
#     iterations = parameter.IntegerParameter(
#         'iterations',
#         default=1000,
#         help_text="Number of iterations")
#
#     nsteps_per_iteration = parameter.IntegerParameter(
#         'nsteps_per_iteration',
#         default=500,
#         help_text="Number of steps per iteration")
#
#     nonbondedCutoff = parameter.DecimalParameter(
#         'nonbondedCutoff',
#         default=10.0,
#         help_text="The non-bonded cutoff in angstroms")
#
#     verbose = parameter.BooleanParameter(
#         'verbose',
#         default=False,
#         help_text="Print verbose YANK logging output")
#
#     rerun = parameter.BooleanParameter(
#         'rerun',
#         default=False,
#         help_text="Start Yank Restart procedure")
#
#     analyze = parameter.BooleanParameter(
#         'analyze',
#         default=False,
#         help_text="Start Yank Analysis on the collected results")
#
#     hmr = parameter.BooleanParameter(
#         'hmr',
#         default=False,
#         description='Hydrogen Mass Repartitioning')
#
#     def begin(self):
#         #self.opt = self.args.__dict__
#         self.opt = vars(self.args)
#         self.opt['Logger'] = self.log
#
#     def process(self, record, port):
#         try:
#             self.log.warn(">>>>>>> {} verbose {}".format(self.title, self.opt['verbose']))
#             self.log.warn(">>>>>>> {} rerun {}".format(self.title, self.opt['rerun']))
#             self.log.warn(">>>>>>> {} analyze {}".format(self.title, self.opt['analyze']))
#             self.log.warn(">>>>>>> {} iterations {}".format(self.title, self.opt['iterations']))
#             self.log.warn(">>>>>>> {} pressure {}".format(self.title, self.opt['pressure']))
#             self.log.warn(">>>>>>> {} temperature {}".format(self.title, self.opt['temperature']))
#             self.log.warn(">>>>>>> {} minimize {}".format(self.title, self.opt['minimize']))
#             self.log.warn(">>>>>>> {} min_parallel {}".format(self.title, self.opt['min_parallel']))
#             self.log.warn(">>>>>>> {} max_parallel {}".format(self.title, self.opt['max_parallel']))
#
#             self.success.emit(record)
#         except:
#             # Attach an error message to the molecule that failed
#             self.log.error(traceback.format_exc())
#             # Return failed mol
#             self.failure.emit(record)
