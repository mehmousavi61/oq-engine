#  -*- coding: utf-8 -*-
#  vim: tabstop=4 shiftwidth=4 softtabstop=4

#  Copyright (c) 2014, GEM Foundation

#  OpenQuake is free software: you can redistribute it and/or modify it
#  under the terms of the GNU Affero General Public License as published
#  by the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.

#  OpenQuake is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.

#  You should have received a copy of the GNU Affero General Public License
#  along with OpenQuake.  If not, see <http://www.gnu.org/licenses/>.

import os
import numpy
import logging
import operator
import collections
from functools import partial

from openquake.hazardlib.site import SiteCollection
from openquake.hazardlib.calc.hazard_curve import (
    hazard_curves_per_trt, zero_curves, zero_maps, agg_curves)
from openquake.hazardlib.calc.filters import source_site_distance_filter, \
    rupture_site_distance_filter
from openquake.risklib import scientific
from openquake.commonlib import parallel
from openquake.commonlib.export import export
from openquake.baselib.general import AccumDict, split_in_blocks, groupby

from openquake.commonlib.calculators import base, calc


HazardCurve = collections.namedtuple('HazardCurve', 'location poes')


@parallel.litetask
def classical(sources, sitecol, gsims_assoc, monitor):
    """
    :param sources:
        a non-empty sequence of sources of homogeneous tectonic region type
    :param sitecol:
        a SiteCollection instance
    :param gsims_assoc:
        associations trt_model_id -> gsims
    :param monitor:
        a monitor instance
    :returns:
        an AccumDict rlz -> curves
    """
    max_dist = monitor.oqparam.maximum_distance
    truncation_level = monitor.oqparam.truncation_level
    imtls = monitor.oqparam.imtls
    trt_model_id = sources[0].trt_model_id
    gsims = gsims_assoc[trt_model_id]
    result = AccumDict()
    curves_by_gsim = hazard_curves_per_trt(
        sources, sitecol, imtls, gsims, truncation_level,
        source_site_filter=source_site_distance_filter(max_dist),
        rupture_site_filter=rupture_site_distance_filter(max_dist))
    for gsim, curves in zip(gsims, curves_by_gsim):
        result[trt_model_id, str(gsim)] = curves
    return result


def agg_prob(acc, val):
    for key in val:
        acc[key] = agg_curves(acc[key], val[key])
    return acc


@base.calculators.add('classical')
class ClassicalCalculator(base.HazardCalculator):
    """
    Classical PSHA calculator
    """
    core_func = classical
    result_kind = 'curves_by_trt_gsim'

    def execute(self):
        """
        Run in parallel `core_func(sources, sitecol, monitor)`, by
        parallelizing on the sources according to their weight and
        tectonic region type.
        """
        monitor = self.monitor(self.core_func.__name__)
        monitor.oqparam = self.oqparam
        sources = list(self.composite_source_model.sources)
        self.zc = zero_curves(len(self.sitecol), self.oqparam.imtls)
        zero = AccumDict((key, self.zc) for key in self.rlzs_assoc)
        gsims_assoc = self.rlzs_assoc.get_gsims_by_trt_id()
        curves_by_trt_gsim = parallel.apply_reduce(
            self.core_func.__func__,
            (sources, self.sitecol, gsims_assoc, monitor),
            agg=agg_prob, acc=zero,
            concurrent_tasks=self.oqparam.concurrent_tasks,
            weight=operator.attrgetter('weight'),
            key=operator.attrgetter('trt_model_id'))
        return curves_by_trt_gsim

    def post_execute(self, result):
        """
        Collect the hazard curves by realization and export them.

        :param result:
            a nested dictionary (trt_id, gsim) -> IMT -> hazard curves
        """
        curves_by_rlz = self.rlzs_assoc.combine_curves(
            result, agg_curves, self.zc)
        oq = self.oqparam
        if oq.individual_curves:
            self.datastore['hcurves', 'hdf5'] = (
                ('rlz-%d' % rlz.ordinal, curves)
                for rlz, curves in curves_by_rlz.iteritems())
        rlzs = self.rlzs_assoc.realizations
        nsites = len(self.sitecol)

        saved = AccumDict()
        if not oq.exports:
            return saved

        # export curves
        exports = oq.exports.split(',')
        if oq.individual_curves:
            for rlz in rlzs:
                smlt_path = '_'.join(rlz.sm_lt_path)
                suffix = ('-ltr_%d' % rlz.ordinal
                          if oq.number_of_logic_tree_samples else '')
                for fmt in exports:
                    fname = 'hazard_curve-smltp_%s-gsimltp_%s%s.%s' % (
                        smlt_path, rlz.gsim_rlz.uid, suffix, fmt)
                    saved += self.export_curves(curves_by_rlz[rlz], fmt, fname)
        if len(rlzs) == 1:  # cannot compute statistics
            [self.mean_curves] = curves_by_rlz.values()
            return saved

        weights = (None if oq.number_of_logic_tree_samples
                   else [rlz.weight for rlz in rlzs])
        curves_by_imt = {imt: [curves_by_rlz[rlz][imt] for rlz in rlzs]
                         for imt in oq.imtls}
        mean = oq.mean_hazard_curves
        if mean:
            self.mean_curves = numpy.array(self.zc)
            for imt in oq.imtls:
                self.mean_curves[imt] = scientific.mean_curve(
                    [curves_by_rlz[rlz][imt] for rlz in rlzs], weights)

        self.quantile = {}
        for q in oq.quantile_hazard_curves:
            self.quantile[q] = qc = numpy.array(self.zc)
            for imt in oq.imtls:
                qc[imt] = scientific.quantile_curve(
                    curves_by_imt[imt], q, weights).reshape((nsites, -1))
        with self.datastore.h5file(('hcurves', 'hdf5')) as h5f:
            if mean:
                h5f['mean'] = self.mean_curves
            for q in self.quantile:
                h5f['quantile-%s' % q] = self.quantile[q]
        for fmt in exports:
            if mean:
                saved += self.export_curves(
                    self.mean_curves, fmt, 'hazard_curve-mean.%s' % fmt)
            for q in self.quantile:
                saved += self.export_curves(
                    self.quantile[q], fmt, 'quantile_curve-%s.%s' % (q, fmt))
        return saved

    def hazard_maps(self, curves_by_imt):
        """
        Compute the hazard maps associated to the curves and returns
        a dictionary of arrays.
        """
        n, p = len(self.sitecol), len(self.oqparam.poes)
        maps = zero_maps((n, p), self.oqparam.imtls)
        for imt in curves_by_imt.dtype.fields:
            maps[imt] = calc.compute_hazard_maps(
                curves_by_imt[imt], self.oqparam.imtls[imt], self.oqparam.poes)
        return maps

    def export_curves(self, curves, fmt, fname):
        """
        :param curves: an array of N curves to export
        :param fmt: the export format ('xml', 'csv', ...)
        :param fname: the name of the exported file
        """
        if hasattr(self, 'tileno'):
            fname += self.tileno
        saved = AccumDict()
        oq = self.oqparam
        export_dir = oq.export_dir
        saved += export(
            ('hazard_curves', fmt), export_dir, fname, self.sitecol, curves,
            oq.imtls, oq.investigation_time)
        if oq.hazard_maps:
            # hmaps is a composite array of shape (N, P)
            hmaps = self.hazard_maps(curves)
            saved += export(
                ('hazard_curves', fmt), export_dir,
                fname.replace('curve', 'map'), self.sitecol,
                hmaps, oq.imtls, oq.investigation_time)
            if oq.uniform_hazard_spectra:
                uhs_curves = calc.make_uhs(hmaps)
                saved += export(
                    ('uhs', fmt), oq.export_dir,
                    fname.replace('curve', 'uhs'),
                    self.sitecol, uhs_curves)
        return saved


def is_effective_trt_model(result_dict, trt_model):
    """
    Returns True on tectonic region types
    which ID in contained in the result_dict.

    :param result_dict: a dictionary with keys (trt_id, gsim)
    """
    return any(trt_model.id == trt_id for trt_id, _gsim in result_dict)


@parallel.litetask
def classical_tiling(calculator, sitecol, tileno, monitor):
    """
    :param calculator:
        a ClassicalCalculator instance
    :param sitecol:
        a SiteCollection instance
    :param tileno:
        the number of the current tile
    :param monitor:
        a monitor instance
    :returns:
        a dictionary file name -> full path for each exported file
    """
    calculator.sitecol = sitecol
    calculator.tileno = '.%04d' % tileno
    result = calculator.execute()
    # build the correct realizations from the (reduced) logic tree
    calculator.rlzs_assoc = calculator.composite_source_model.get_rlzs_assoc(
        partial(is_effective_trt_model, result))
    n_levels = sum(len(imls) for imls in calculator.oqparam.imtls.itervalues())
    tup = len(calculator.sitecol), n_levels, len(calculator.rlzs_assoc)
    logging.info('Processed tile %d, (sites, levels, keys)=%s', tileno, tup)
    # export the calculator outputs
    saved = calculator.post_execute(result)
    return saved


@base.calculators.add('classical_tiling')
class ClassicalTilingCalculator(ClassicalCalculator):
    """
    Classical Tiling calculator
    """
    prefilter = False
    result_kind = 'pathname_by_fname'

    def execute(self):
        """
        Split the computation by tiles which are run in parallel.
        """
        monitor = self.monitor(self.core_func.__name__)
        monitor.oqparam = self.oqparam
        self.tiles = map(SiteCollection, split_in_blocks(
            self.sitecol, self.oqparam.concurrent_tasks or 1))
        self.oqparam.concurrent_tasks = 0
        calculator = ClassicalCalculator(self.oqparam, monitor)
        calculator.composite_source_model = self.composite_source_model
        calculator.rlzs_assoc = self.composite_source_model.get_rlzs_assoc(
            lambda tm: True)  # build the full logic tree
        all_args = [(calculator, tile, i, monitor)
                    for (i, tile) in enumerate(self.tiles)]
        return parallel.starmap(classical_tiling, all_args).reduce()

    def post_execute(self, result):
        """
        Merge together the exported files for each tile.

        :param result: a dictionary key -> exported filename
        """
        # group files by name; for instance the file names
        # ['quantile_curve-0.1.csv.0000', 'quantile_curve-0.1.csv.0001',
        # 'hazard_map-mean.csv.0000', 'hazard_map-mean.csv.0001']
        # are grouped in the dictionary
        # {'quantile_curve-0.1.csv': ['quantile_curve-0.1.csv.0000',
        #                             'quantile_curve-0.1.csv.0001'],
        # 'hazard_map-mean.csv': ['hazard_map-mean.csv.0000',
        #                         'hazard_map-mean.csv.0001'],
        # }
        dic = groupby((fname for fname in result.itervalues()),
                      lambda fname: fname.rsplit('.', 1)[0])
        # merge together files coming from different tiles in order
        d = {}
        for fname in dic:
            with open(fname, 'w') as f:
                for tilename in sorted(dic[fname]):
                    f.write(open(tilename).read())
                    os.remove(tilename)
            d[os.path.basename(fname)] = fname
        return d
