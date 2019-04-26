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
#
import os

from artemis.wrappers import (WorkFloeWrapper,
                              DatasetWrapper,
                              OutputDatasetWrapper)

from artemis.test import FloeTestCase

from artemis.decorators import package

import pytest

import MDOrion

from openeye import oechem

from datarecord import read_mol_record

PACKAGE_DIR = os.path.dirname(os.path.dirname(MDOrion.__file__))

FILE_DIR = os.path.join(PACKAGE_DIR, "tests", "data")
FLOES_DIR = os.path.join(PACKAGE_DIR, "floes")

os.chdir(FILE_DIR)


@package(PACKAGE_DIR)
class TestMDOrionFloes(FloeTestCase):

    @pytest.mark.local
    @pytest.mark.orion
    def test_omm_simple_MD(self):
        workfloe = WorkFloeWrapper.get_workfloe(
            os.path.join(FLOES_DIR, "SimpleMD.py"),
            run_timeout=43200,
            queue_timeout=2000
        )
        protein_file = DatasetWrapper.get_dataset(
            os.path.join(
                FILE_DIR,
                "4JOO_truncNoLig.pdb"
            )
        )

        output_file = OutputDatasetWrapper(extension=".oedb")
        fail_output_file = OutputDatasetWrapper(extension=".oedb")

        workfloe.start(
            {
                "promoted": {
                    "system": protein_file.identifier,
                    "prod_ns": 1,
                    "out": output_file.identifier,
                    "fail": fail_output_file.identifier
                }
            }
        )

        self.assertWorkFloeComplete(workfloe)

        fail_ifs = oechem.oeifstream()
        records_fail = []

        while True:
            record_fail = read_mol_record(fail_ifs)
            if record_fail is None:
                break
            records_fail.append(record_fail)
        fail_ifs.close()

        count = len(records_fail)
        # The fail record must be empty
        self.assertEqual(count, 0)

        # Check output
        ifs = oechem.oeifstream(output_file.path)
        records = []

        while True:
            record = read_mol_record(ifs)
            if record is None:
                break
            records.append(record)
        ifs.close()

        count = len(records)
        # Check the out record list
        self.assertEqual(count, 1)

    @pytest.mark.local
    @pytest.mark.orion
    def test_gmx_Simple_floe(self):
        workfloe = WorkFloeWrapper.get_workfloe(
            os.path.join(FLOES_DIR, "SimpleMD.py"),
            run_timeout=43200,
            queue_timeout=2000
        )

        protein_file = DatasetWrapper.get_dataset(
            os.path.join(
                FILE_DIR,
                "4JOO_truncNoLig.pdb"
            )
        )

        output_file = OutputDatasetWrapper(extension=".oedb")
        fail_output_file = OutputDatasetWrapper(extension=".oedb")

        workfloe.start(
            {
                "promoted": {
                    "system": protein_file.identifier,
                    "md_engine": "Gromacs",
                    "prod_ns": 1,
                    "out": output_file.identifier,
                    "fail": fail_output_file.identifier
                }
            }
        )

        self.assertWorkFloeComplete(workfloe)

        fail_ifs = oechem.oeifstream()
        records_fail = []

        while True:
            record_fail = read_mol_record(fail_ifs)
            if record_fail is None:
                break
            records_fail.append(record_fail)
        fail_ifs.close()

        count = len(records_fail)
        # The fail record must be empty
        self.assertEqual(count, 0)

        # Check output
        ifs = oechem.oeifstream(output_file.path)
        records = []

        while True:
            record = read_mol_record(ifs)
            if record is None:
                break
            records.append(record)
        ifs.close()

        count = len(records)
        # Check the out record list
        self.assertEqual(count, 1)