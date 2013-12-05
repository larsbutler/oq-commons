#  -*- coding: utf-8 -*-
#  vim: tabstop=4 shiftwidth=4 softtabstop=4

#  Copyright (c) 2013, GEM Foundation

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

"""
This module contains converter classes working on nodes of kind

- vulnerabilitymodel
- fragilitymodel
- exposuremodel
- gmfset
- gmfcollection
"""
import itertools
from openquake.risklib import scientific
from openquake.nrmllib.node import Node
from openquake.common import record, records


def groupby(records, keyfields):
    """
    Group the records on the underlying CSV according to the given
    keyfield. Assume the records are sorted.
    """
    return itertools.groupby(records, lambda r: [r[k] for k in keyfields])


class Converter(object):
    """
    Base class.
    """

    @classmethod
    def from_node(cls, node):
        """
        Return a specialized Converter instance
        """
        tag = node.tag
        name = tag[0].upper() + tag[1:]
        clsname = name[:-5] if name.endswith('Model') else name
        if 'format' in node.attrib:  # for fragility functions
            clsname += node['format'].capitalize()
        if clsname == 'GmfSet':
            clsname = 'GmfCollection'
        convertertype = globals()[clsname]
        tset = record.TableSet(convertertype)
        tset.insert_all(convertertype.node_to_records(node))
        return convertertype(tset)

    @classmethod
    def node_to_records(cls, node):
        """Convert the node into a sequence of records"""
        raise NotImplementedError

    @classmethod
    def recordtypes(cls):
        """
        Get the record classes associated to the given converter class,
        in order
        """
        rectypes = []
        for val in vars(records).itervalues():
            if (isinstance(val, record.MetaRecord) and
                    val.convertername == cls.__name__):
                rectypes.append(val)
        return sorted(rectypes, key=lambda rt: rt._ordinal)

    def __init__(self, tableset):
        self.tableset = tableset

    def __repr__(self):
        return '<%s>' % self.__class__.__name__

    def to_node(self):
        raise NotImplementedError


############################# vulnerability #################################

class Vulnerability(Converter):
    """A converter for vulnerabilityModel nodes"""

    @classmethod
    def node_to_records(cls, node):
        """Convert the node into a sequence of Vulnerability records"""
        for vset in node.getnodes('discreteVulnerabilitySet'):
            set_id = vset['vulnerabilitySetID']
            dvs = records.DiscreteVulnerabilitySet(
                set_id,
                vset['assetCategory'],
                vset['lossCategory'],
                vset.IML['IMT'])
            yield dvs
            imls = vset.IML.text.split()
            for vf in vset.getnodes('discreteVulnerability'):
                fun_id = vf['vulnerabilityFunctionID']
                ratios = vf.lossRatio.text.split()
                coeffs = vf.coefficientsVariation.text.split()
                dv = records.DiscreteVulnerability(
                    set_id,
                    fun_id,
                    vf['probabilisticDistribution'])
                yield dv
                for iml, ratio, coeff in zip(imls, ratios, coeffs):
                    yield records.DiscreteVulnerabilityData(
                        set_id, fun_id, iml, ratio, coeff)

    def to_node(self):
        tset = self.tableset
        dvs_node = record.nodedict(tset.tableDiscreteVulnerabilitySet)
        dvf_node = record.nodedict(tset.tableDiscreteVulnerability)
        for (set_id, vf_id), group in groupby(
                tset.tableDiscreteVulnerabilityData,
                ['vulnerabilitySetID', 'vulnerabilityFunctionID']):
            dvf = dvf_node[set_id, vf_id]
            coeffs = []
            ratios = []
            imls = []
            for row in group:
                imls.append(row['IML'])
                coeffs.append(row['coefficientsVariation'])
                ratios.append(row['lossRatio'])

            # check that we can instantiate a VulnerabilityFunction in risklib
            scientific.VulnerabilityFunction(
                map(float, imls), map(float, ratios), map(float, coeffs))

            dvf.lossRatio.text = ' '.join(ratios)
            dvf.coefficientsVariation.text = ' '.join(coeffs)
            dvs_node[(set_id,)].append(dvf)
            dvs_node[(set_id,)].IML.text = ' '.join(imls)
        return Node('vulnerabilityModel', nodes=dvs_node.values())


############################# fragility #################################

class FragilityDiscrete(Converter):
    """A converter for fragilityModel nodes"""

    @classmethod
    def node_to_records(cls, node):
        """Convert the node into a sequence of Fragility records"""
        fmt = node['format']
        assert fmt == 'discrete'
        limitStates = node.limitStates.text.split()
        yield records.FragilityDiscrete(
            fmt, node.description.text.strip(), node.limitStates.text.strip())
        for i, ffs in enumerate(node.getnodes('ffs'), 1):
            ffs_ordinal = str(i)
            yield records.FFSetDiscrete(
                ffs_ordinal,
                ffs.taxonomy.text,
                ffs.attrib.get('noDamageLimit', ''),
                ffs.IML['IMT'],
                ffs.IML['imlUnit'])
            imls = ffs.IML.text.split()
            for ls, ffd in zip(limitStates, ffs.getnodes('ffd')):
                assert ls == ffd['ls'], 'Expected %s, got %s' % (
                    ls, ffd['ls'])
                yield records.FFLimitStateDiscrete(ffs_ordinal, ls)
                poEs = ffd.poEs.text.split()
                for iml, poe in zip(imls, poEs):
                    yield records.FFDataDiscrete(ffs_ordinal, ls, iml, poe)

    def to_node(self):
        """
        Build a full fragility node from CSV
        """
        tset = self.tableset
        frag = tset.tableFragilityDiscrete[0].to_node()
        ffs_node = record.nodedict(tset.tableFFSetDiscrete)
        nodamage = float(ffs_node['noDamageLimit']) \
            if 'noDamageLimit' in ffs_node else None
        frag.nodes.extend(ffs_node.values())
        for (ordinal, ls), data in groupby(
                tset.tableFFDataDiscrete, ['ffs_ordinal', 'limitState']):
            data = list(data)

            # check that we can instantiate a FragilityFunction in risklib
            if nodamage:
                scientific.FragilityFunctionDiscrete(
                    [nodamage] + [rec.iml for rec in data],
                    [0.0] + [rec.poe for rec in data], nodamage)
            else:
                scientific.FragilityFunctionDiscrete(
                    [rec.iml for rec in data],
                    [rec.poe for rec in data], nodamage)

            imls = ' '.join(rec['iml'] for rec in data)
            ffs_node[(ordinal,)].IML.text = imls
            poes = ' '.join(rec['poe'] for rec in data)
            n = Node('ffd', dict(ls=ls))
            n.append(Node('poEs', text=poes))
            ffs_node[(ordinal,)].append(n)
        return frag


class FragilityContinuous(Converter):
    """A converter for fragilityModel nodes"""

    @classmethod
    def node_to_records(cls, node):
        """Convert the node into a sequence of Fragility records"""
        fmt = node['format']
        assert fmt == 'continuous', fmt
        limitStates = node.limitStates.text.split()
        yield records.FragilityContinuous(
            fmt, node.description.text.strip(), node.limitStates.text.strip())
        for i, ffs in enumerate(node.getnodes('ffs'), 1):
            ffs_ordinal = str(i)
            yield records.FFSetContinuous(
                ffs_ordinal,
                ffs.taxonomy.text,
                ffs.attrib.get('noDamageLimit', ''),
                ffs.attrib.get('type', ''),
                ffs.IML['IMT'],
                ffs.IML['imlUnit'],
                ffs.IML['minIML'],
                ffs.IML['maxIML'])
            for ls, ffc in zip(limitStates, ffs.getnodes('ffc')):
                assert ls == ffc['ls'], 'Expected %s, got %s' % (
                    ls, ffc['ls'])
                yield records.FFLimitStateContinuous(ffs_ordinal, ls)
                yield records.FFDContinuos(
                    ffs_ordinal, ls, 'mean', ffc.params['mean'])
                yield records.FFDContinuos(
                    ffs_ordinal, ls, 'stddev', ffc.params['stddev'])

    def to_node(self):
        """
        Build a full continuous fragility node from CSV
        """
        tset = self.tableset
        frag = tset.tableFragilityContinuous[0].to_node()
        ffs_node = record.nodedict(tset.tableFFSetContinuous)
        frag.nodes.extend(ffs_node.values())
        for (ordinal, ls), data in groupby(
                tset.tableFFDContinuos, ['ffs_ordinal', 'limitState']):
            data = list(data)
            n = Node('ffc', dict(ls=ls))
            param = dict(row[2:] for row in data)  # param, value

            # check that we can instantiate a FragilityFunction in risklib
            scientific.FragilityFunctionContinuous(
                float(param['mean']), float(param['stddev']))

            n.append(Node('params', param))
            ffs_node[(ordinal,)].append(n)
        return frag


############################# exposure #################################

COSTCOLUMNS = 'value deductible insuranceLimit retrofitted'.split()
PERIODS = 'day', 'night', 'transit', 'early_morning', 'late_afternoon'
## TODO: the occupancy periods should be inferred from the NRML file,
## not hardcoded, exactly as the cost types
## NB: they must be valid Python names, with no spaces inside


def getcosts(asset, costcolumns):
    """
    Extracts different costs from an asset node. If a cost is not available
    returns an empty string for it. Returns a list with the same length of
    the cost columns.
    """
    row = dict.fromkeys(costcolumns, '')
    for cost in asset.costs:
        for kind in COSTCOLUMNS:
            row['%s__%s' % (cost['type'], kind)] = cost.attrib.get(kind, '')
    return [row[cc] for cc in costcolumns]


def getcostcolumns(costtypes):
    """
    Extracts the kind of costs from a CostTypes node. Those will correspond
    to columns names in the .csv representation of the exposure.
    """
    cols = []
    for cost in costtypes:
        for kind in COSTCOLUMNS:
            cols.append('%s__%s' % (cost['name'], kind))
    return cols


def getoccupancies(asset):
    """
    Extracts the occupancies from an asset node.
    """
    dic = dict(('occupancy__' + occ['period'], occ['occupants'])
               for occ in asset.occupancies)
    return [dic.get('occupancy__%s' % period, '') for period in PERIODS]


def assetgenerator(assets, location_node, costtypes):
    """
    Convert assets into asset nodes.

    :param assets: an iterable over dictionaries
    :param costtypes: list of dictionaries with the cost types

    :returns: an iterable over Node objects describing exposure assets
    """
    for asset in assets:
        nodes = [location_node[(asset['location'],)]]
        costnodes = []
        for costtype in costtypes:
            keepnode = True
            attr = dict(type=costtype['name'])
            for costcol in COSTCOLUMNS:
                value = asset['%s.%s' % (costtype['name'], costcol)]
                if value:
                    attr[costcol] = value
                elif costcol == 'value':
                    keepnode = False  # ignore costs without value
            if keepnode:
                costnodes.append(Node('cost', attr))
        if costnodes:
            nodes.append(Node('costs', {}, nodes=costnodes))
        has_occupancies = any('occupancy__%s' % period in asset
                              for period in PERIODS)
        if has_occupancies:
            occ = []
            for period in PERIODS:
                occupancy = asset['occupancy__' + period]
                if occupancy:
                    occ.append(Node('occupancy',
                                    dict(occupants=occupancy, period=period)))
            nodes.append(Node('occupancies', {}, nodes=occ))
        attr = dict(id=asset['id'], number=asset['number'],
                    taxonomy=asset['taxonomy'])
        if 'area' in asset:
            attr['area'] = asset['area']
        yield Node('asset', attr, nodes=nodes)


class Exposure(Converter):
    """A converter for exposureModel nodes"""

    @classmethod
    def node_to_records(cls, node):
        """
        Convert the node into a sequence of Exposure records
        """
        if node['category'] == 'buildings':
            for c in node.conversions.costTypes:
                yield records.CostType(c['name'], c['type'], c['unit'],
                                       c.attrib.get('retrofittedType', ''),
                                       c.attrib.get('retrofittedUnit', ''))
            #costcolumns = getcostcolumns(node.conversions.costTypes)
            conv = node.conversions
            yield records.Exposure(
                node['id'],
                node['category'],
                node['taxonomySource'],
                node.description.text.strip(),
                conv.area['type'],
                conv.area['unit'],
                conv.deductible['isAbsolute'],
                conv.insuranceLimit['isAbsolute'])
        else:
            yield records.Exposure(
                node['id'],
                node['category'],
                node['taxonomySource'],
                node.description.text.strip())

        locations = {}  # location -> id
        loc_counter = itertools.count(1)
        for asset in node.assets:
            # getcosts(asset, costcolumns) + getoccupancies(asset)
            loc = asset.location['lon'], asset.location['lat']
            try:
                loc_id = locations[loc]
            except KeyError:
                loc_id = locations[loc] = loc_counter.next()

            yield records.Location(str(loc_id), loc[0], loc[1])
            yield records.Asset(
                asset['id'], asset['taxonomy'],  asset['number'],
                asset.attrib.get('area', ''), loc_id)

    def to_node(self):
        """
        Build a Node object containing a full exposure from a set
        of CSV files. For population exposure the CSV has a form like

          id,taxonomy,lon,lat,number
          asset_01,IT-PV,9.15000,45.16667,7
          asset_02,IT-CE,9.15333,45.12200,7

        whereas for building has a form like

          id,taxonomy,lon,lat,number,area,cost__value,..., occupancy__day
          asset_01,RC/DMRF-D/LR,9.15000,45.16667,7,120,40,.5,...,20
          asset_02,RC/DMRF-D/HR,9.15333,45.12200,7,119,40,,,...,20
          asset_03,RC/DMRF-D/LR,9.14777,45.17999,5,118,,...,,5

        with a variable number of columns depending on the metadata.
        """
        tset = self.tableset
        exp = tset.tableExposure[0].to_node()
        if exp['category'] == 'buildings':
            exp.conversions.costTypes.nodes = ctypes = [
                c.to_node() for c in tset.tableCostType]
            # costcolumns = getcostcolumns(exp.conversions.costTypes)
        else:
            ctypes = []
        location_dict = record.nodedict(tset.tableLocation)
        exp.assets.nodes = assetgenerator(
            tset.tableAsset, location_dict, ctypes)
        return exp


################################# gmf ##################################

class GmfCollection(Converter):
    """A converter for gmfSet/GmfCollection nodes"""

    @classmethod
    def node_to_records(cls, node):
        """
        Convert the node into a sequence of Gmf records
        """
        if node.tag == 'gmfSet':
            yield records.GmfSet('0', '')
            for gmf in node.getnodes('gmf'):
                imt = gmf['IMT']
                if imt == 'SA':
                    imt += '(%s)' % gmf['saPeriod']
                yield records.Gmf(1, imt, '')
                for n in gmf:
                    yield records.GmfData(
                        '0', imt, '', n['lon'], n['lat'], n['gmv'])
            return
        yield records.GmfCollection(
            node['sourceModelTreePath'],
            node['gsimTreePath'])
        for gmfset in node.getnodes('gmfSet'):
            ses_id = gmfset['stochasticEventSetId']
            yield records.GmfSet(ses_id, gmfset['investigationTime'])
            for gmf in gmfset.getnodes('gmf'):
                rup = gmf['ruptureId']
                imt = gmf['IMT']
                if imt == 'SA':
                    imt += '(%s)' % gmf['saPeriod']
                yield records.Gmf(ses_id, imt, rup)
                for n in gmf:
                    yield records.GmfData(
                        ses_id, imt, rup, n['lon'], n['lat'], n['gmv'])

    def _to_node(self):
        """
        Add to a gmfset node all the data from a file GmfData.csv of the form::

         stochasticEventSetId,imtStr,ruptureId,lon,lat,gmv
         1,SA(0.025),,0.0,0.0,0.2
         1,SA(0.025),,1.0,0.0,1.4
         1,SA(0.025),,0.0,1.0,0.6
         1,PGA,,0.0,0.0,0.2
         1,PGA,,1.0,0.0,1.4
         1,PGA,,0.0,1.0,0.6

        The rows are grouped by ses, imt, rupture.
        """
        tset = self.tableset
        gmfset_node = record.nodedict(tset.tableGmfSet)
        for (ses, imt, rupture), rows in groupby(
                tset.tableGmfData,
                ['stochasticEventSetId', 'imtStr', 'ruptureId']):
            if imt.startswith('SA'):
                attr = dict(IMT='SA', saPeriod=imt[3:-1], saDamping='5')
            else:
                attr = dict(IMT=imt)
            if rupture:
                attr['ruptureId'] = rupture
            nodes = [records.GmfData(*r).to_node() for r in rows]
            gmfset_node[(ses,)].append(Node('gmf', attr, nodes=nodes))
        return gmfset_node

    def to_node(self):
        """
        Build a gmfCollection node from GmfCollection.csv,
        GmfSet.csv and GmfData.csv
        """
        tset = self.tableset
        try:
            gmfcoll = tset.tableGmfCollection[0]
        except IndexError:  # no data for GmfCollection
            gmfset_node = self._to_node()
            return gmfset_node.values()[0]  # there is a single node
        gmfset_node = self._to_node()
        gmfcoll_node = gmfcoll.to_node()
        for node in gmfset_node.values():
            gmfcoll_node.append(node)
        return gmfcoll_node