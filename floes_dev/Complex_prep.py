#!/usr/bin/env python

# (C) 2019 OpenEye Scientific Software Inc. All rights reserved.
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

from floe.api import WorkFloe

from orionplatform.cubes import DatasetReaderCube, DatasetWriterCube

from MDOrion.LigPrep.cubes import LigandSetting
from MDOrion.LigPrep.cubes import ParallelLigandChargeCube

from MDOrion.System.cubes import IDSettingCube

from MDOrion.ProtPrep.cubes import ProteinSetting

from MDOrion.ComplexPrep.cubes import ComplexPrepCube

from MDOrion.System.cubes import ParallelSolvationCube

from MDOrion.ForceField.cubes import ParallelForceFieldCube

job = WorkFloe("Complex Preparation",
               title="Complex Preparation")

job.description = """
Complex Preparation Workflow

Ex. python floes/openmm_complex_prep.py --protein protein.oeb
--ligands ligands.oeb  --ofs-data_out complex.oeb

Parameters:
-----------
protein (file): OEB file of the prepared protein
ligands (file): OEB file of the prepared ligands


Outputs:
--------
ofs: Output file
"""

job.classification = [['Simulation']]
job.tags = [tag for lists in job.classification for tag in lists]

# Ligand setting
iligs = DatasetReaderCube("Ligand Reader", title="Ligand Reader")
iligs.promote_parameter("data_in", promoted_name="ligands", title="Ligand Input File", description="Ligand file name")


chargelig = ParallelLigandChargeCube("LigCharge", title='Ligand Charge')
chargelig.promote_parameter('charge_ligands', promoted_name='charge_ligands',
                            description="Charge the ligand or not", default=True)

ligset = LigandSetting("LigandSetting")
ligset.set_parameters(lig_res_name='LIG')

ligid = IDSettingCube("Ligand Ids")

iprot = DatasetReaderCube("Protein Reader", title="Protein Reader")
iprot.promote_parameter("data_in", promoted_name="protein", title="Protein Input File", description="Protein file name")


protset = ProteinSetting("ProteinSetting")

complx = ComplexPrepCube("Complex")
complx.set_parameters(lig_res_name='LIG')

solvate = ParallelSolvationCube("Hydration", title='System Hydration')
solvate.promote_parameter('density', promoted_name='density', default=1.03,
                          description="Solution density in g/ml")
solvate.promote_parameter('salt_concentration', promoted_name='salt_concentration', default=50.0,
                          description='Salt concentration (Na+, Cl-) in millimolar')
solvate.set_parameters(close_solvent=True)

ff = ParallelForceFieldCube("ForceField", title="System Parametrization")
ff.promote_parameter('protein_forcefield', promoted_name='protein_ff', default='Amber99SBildn')
ff.promote_parameter('ligand_forcefield', promoted_name='ligand_ff', default='Gaff2')
ff.promote_parameter('other_forcefield', promoted_name='other_ff', default='Gaff2')
ff.set_parameters(lig_res_name='LIG')

ofs = DatasetWriterCube('ofs', title='Out')
ofs.promote_parameter("data_out", promoted_name="out")

fail = DatasetWriterCube('fail', title='Failures')
fail.promote_parameter("data_out", promoted_name="fail")

job.add_cubes(iligs, chargelig, ligset, ligid,
              iprot, protset, complx, solvate, ff, ofs, fail)

iligs.success.connect(ligset.intake)
ligset.success.connect(chargelig.intake)
chargelig.success.connect(ligid.intake)
ligid.success.connect(complx.intake)
iprot.success.connect(protset.intake)
protset.success.connect(complx.protein_port)
complx.success.connect(solvate.intake)
solvate.success.connect(ff.intake)
ff.success.connect(ofs.intake)
ff.failure.connect(fail.intake)

if __name__ == "__main__":
    job.run()
