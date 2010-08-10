#!/usr/bin/env python
# vim: tabstop=4 shiftwidth=4 softtabstop=4
# encoding: utf-8

import unittest
from opengem import flags

FLAGS = flags.FLAGS

class flags_unittest(unittest.TestCase):
    
    def setUp(self):
        FLAGS.Reset()
    
    def assertDefaultValueIs(self, parameter, expected):
        FLAGS.Reset() # back to default values
        self.assertEqual(expected, FLAGS[parameter].default)
    
    def parse(self, argv):
        FLAGS(argv)
    
    def test_accepts_the_population_exposure(self):
        self.assertTrue(flags.POPULATION_EXPOSURE in FLAGS.RegisteredFlags(), 'Must accept the population exposure')

    def test_accepts_the_countries_exposure(self):
        self.assertTrue(flags.COUNTRIES_EXPOSURE in FLAGS.RegisteredFlags(), 'Must accept the countries exposure')

    def test_accepts_the_upper_left_region_corner(self):
        self.assertTrue(flags.FROM in FLAGS.RegisteredFlags(), 'Must accept the upper left region corner')

    def test_accepts_the_lower_right_region_corner(self):
        self.assertTrue(flags.TO in FLAGS.RegisteredFlags(), 'Must accept the lower right region corner')

    def test_accepts_the_cell_size_of_the_region(self):
        self.assertTrue(flags.CELL_SIZE in FLAGS.RegisteredFlags(), 'Must accept the cell size of the region')
        self.assertDefaultValueIs(flags.CELL_SIZE, 0.00833333333333)

    def test_mandatory_parameters(self):
        self.parse(['./RISKENGINE'])
        self.assertRaises(flags.FlagsError, flags.check_mandatory_parameters)
        
        self.parse(['./RISKENGINE', '--population-exposure=/path'])
        self.assertRaises(flags.FlagsError, flags.check_mandatory_parameters)
        
        self.parse(['./RISKENGINE', '--population-exposure=/path', '--countries-exposure=/path'])
        self.assertRaises(flags.FlagsError, flags.check_mandatory_parameters)
        
        self.parse(['./RISKENGINE', '--population-exposure=/path', '--countries-exposure=/path', '--from=1.0,2.0'])
        self.assertRaises(flags.FlagsError, flags.check_mandatory_parameters)
        
        # all mandatory parameters filled
        self.parse(['./RISKENGINE', '--population-exposure=/path', '--countries-exposure=/path', '--from=1.0,2.0', '--to=1.0,2.0'])
        flags.check_mandatory_parameters
    
    def test_accepts_the_fixed_cov_iml(self):
        self.assertTrue(flags.FIXED_COV_IML in FLAGS.RegisteredFlags(), 'Must accept the fixed cov iml')
        self.assertDefaultValueIs(flags.FIXED_COV_IML, 0.001)
    
    def test_accepts_the_fixed_mean_iml(self):
        self.assertTrue(flags.FIXED_MEAN_IML in FLAGS.RegisteredFlags(), 'Must accept the fixed mean iml')
        self.assertDefaultValueIs(flags.FIXED_MEAN_IML, 6.5)
    
    def test_accepts_the_probability_for_the_loss_maps(self):
        self.assertTrue(flags.LOSS_MAPS_PROBABILITY in FLAGS.RegisteredFlags(), 'Must accept the probability to use for the loss maps')
        self.assertDefaultValueIs(flags.LOSS_MAPS_PROBABILITY, 0.1)
