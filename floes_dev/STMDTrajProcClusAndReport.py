#!/usr/bin/env python

from floe.api import WorkFloe

from orionplatform.cubes import DatasetReaderCube, DatasetWriterCube

from MDOrion.TrjAnalysis.cubes import ParallelTrajToOEMolCube

from MDOrion.TrjAnalysis.cubes import ParallelTrajInteractionEnergyCube

from MDOrion.TrjAnalysis.cubes import ParallelTrajPBSACube

from MDOrion.TrjAnalysis.cubes import ParallelClusterOETrajCube

from MDOrion.TrjAnalysis.cubes import ParallelMDTrajAnalysisClusterReport

from MDOrion.TrjAnalysis.cubes import MDFloeReportCube

job = WorkFloe("Analysing Trajectory from Short Trajectory MD")

job.description = """
Analyse the trajectory from Short Trajectory MD in terms of interactions between the
ligand and the active site and in terms of ligand RMSD after fitting the trajectory
based on active site C_alphas.

Required Input Parameters:
--------------------------
in: Collection of OERecords (one per ligand) of Short Trajectory MD results.

Outputs:
--------
floe report: html page of the Analysis for each ligand.
out (.oedb file): file of the Analysis results for all ligands.
"""

ifs = DatasetReaderCube("ifs")
ifs.promote_parameter("data_in", promoted_name="in", title="System Input OERecord", description="OERecord file name")

ofs = DatasetWriterCube('ofs', title='OFS-Success')
ofs.promote_parameter("data_out", promoted_name="out", title="System Output OERecord", description="OERecord file name")

trajToOEMol = ParallelTrajToOEMolCube("TrajToOEMolCube")
trajIntE = ParallelTrajInteractionEnergyCube("TrajInteractionEnergyCube")
trajPBSA = ParallelTrajPBSACube("TrajPBSACube")
clusCube = ParallelClusterOETrajCube("ClusterOETrajCube")
molHtml = ParallelMDTrajAnalysisClusterReport("MolHtmlCube")
floeReport = MDFloeReportCube("FloeReportCube")

job.add_cubes(ifs, trajToOEMol, trajIntE, trajPBSA, clusCube, molHtml, floeReport, ofs)

ifs.success.connect(trajToOEMol.intake)
trajToOEMol.success.connect(trajIntE.intake)
trajIntE.success.connect(trajPBSA.intake)
trajPBSA.success.connect(clusCube.intake)
clusCube.success.connect(molHtml.intake)
molHtml.success.connect(floeReport.intake)
floeReport.success.connect(ofs.intake)

if __name__ == "__main__":
    job.run()