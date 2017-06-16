import logging
import os
import time

import pickle
from tqdm import tqdm
from mrtarget.common.ElasticsearchQuery import ESQuery
from mrtarget.modules.ChEMBL import ChEMBLLookup
from mrtarget.modules.ECO import ECOLookUpTable
from mrtarget.modules.EFO import EFOLookUpTable
from mrtarget.modules.HPA import HPALookUpTable
from mrtarget.modules.GeneData import GeneLookUpTable
from mrtarget.modules.Literature import LiteratureLookUpTable
from mrtarget.modules.Ontology import OntologyClassReader
from mrtarget.Settings import Config, file_or_resource


class LookUpData():
    def __init__(self):
        self.available_genes = None
        self.available_efos = None
        self.available_ecos = None
        self.available_hpa = None
        self.uni2ens = None
        self.non_reference_genes = None
        self.available_gene_objects = None
        self.available_efo_objects = None
        self.available_eco_objects = None
        self.chembl = None


class LookUpDataType(object):
    TARGET = 'target'
    DISEASE = 'disease'
    EFO = 'efo'
    ECO = 'eco'
    PUBLICATION = 'publication'
    MP = 'mp'
    HPO = 'hpo'
    CHEMBL_DRUGS = 'chembl_drugs'
    HPA = 'hpa'


class LookUpDataRetriever(object):
    def __init__(self,
                 es=None,
                 r_server=None,
                 targets=[],
                 data_types=(LookUpDataType.TARGET,
                             LookUpDataType.DISEASE,
                             LookUpDataType.ECO),
                 autoload=True):

        self.es = es
        self.r_server = r_server
        if es is not None:
            self.esquery = ESQuery(es)
        self.lookup = LookUpData()
        self.logger = logging.getLogger(__name__)

        # TODO: run the steps in parallel to speedup loading times
        for dt in tqdm(data_types,
                       desc='loading lookup data',
                       unit=' steps',
                       leave=False):
            start_time = time.time()
            if dt == LookUpDataType.TARGET:
                self._get_gene_info(targets, autoload=autoload)
            elif dt == LookUpDataType.DISEASE:
                self._get_available_efos()
            elif dt == LookUpDataType.ECO:
                self._get_available_ecos()
            elif dt == LookUpDataType.MP:
                self._get_mp()
            elif dt == LookUpDataType.HPO:
                self._get_hpo()
            elif dt == LookUpDataType.EFO:
                self._get_efo()
            elif dt == LookUpDataType.PUBLICATION:
                self._get_available_publications()
            elif dt == LookUpDataType.CHEMBL_DRUGS:
                self._get_available_chembl_mappings()
            elif dt == LookUpDataType.HPA:
                self._get_available_hpa()

            self.logger.info("finished loading %s data into redis, took %ss" %(dt, str(time.time() - start_time)))

    def _get_available_efos(self):
        self.logger.info('getting efos')
        self.lookup.available_efos = EFOLookUpTable(self.es, 'EFO_LOOKUP', self.r_server)

    def _get_available_ecos(self):
        self.logger.info('getting ecos')
        self.lookup.available_ecos = ECOLookUpTable(self.es, 'ECO_LOOKUP', self.r_server)


    def _get_gene_info(self, targets=[], autoload = True):
        self.logger.info('getting gene info')
        self.lookup.available_genes = GeneLookUpTable(self.es,
                                                      'GENE_LOOKUP',
                                                      self.r_server,
                                                      targets = targets,
                                                      autoload = autoload)
        self.lookup.uni2ens = self.lookup.available_genes.uniprot2ensembl
        self._get_non_reference_gene_mappings()

    def _get_non_reference_gene_mappings(self):
        self.lookup.non_reference_genes = {}
        skip_header=True
        for line in file(file_or_resource('genes_with_non_reference_ensembl_ids.tsv')):
            if skip_header:
                skip_header=False
            symbol, ensg, assembly, chr, is_ref = line.split()
            if symbol not in self.lookup.non_reference_genes:
                self.lookup.non_reference_genes[symbol]=dict(reference='',
                                                      alternative=[])
            if is_ref == 't':
                self.lookup.non_reference_genes[symbol]['reference']=ensg
            else:
                self.lookup.non_reference_genes[symbol]['alternative'].append(ensg)

    def _get_hpo(self):
        '''
        Load HPO to accept phenotype terms that are not in EFO
        :return:
        '''
        cache_file = 'processed_hpo_lookup'
        obj = self._get_from_pickled_file_cache(cache_file)
        if obj is None:
            obj = OntologyClassReader()
            obj.load_hpo_classes()
            obj.rdf_graph = None
            self._set_in_pickled_file_cache(obj, cache_file)
        self.lookup.hpo_ontology = obj

    def _get_mp(self):
        '''
        Load MP to accept phenotype terms that are not in EFO
        :return:
        '''
        cache_file = 'processed_mp_lookup'
        obj = self._get_from_pickled_file_cache(cache_file)
        if obj is None:
            obj = OntologyClassReader()
            obj.load_mp_classes()
            obj.rdf_graph = None
            self._set_in_pickled_file_cache(obj, cache_file)
        self.lookup.mp_ontology = obj



    def _get_efo(self):
        '''
        Load EFO current and obsolete classes to report them to data providers
        :return:
        '''
        cache_file = 'processed_efo_lookup'
        obj = self._get_from_pickled_file_cache(cache_file)
        if obj is None:
            obj = OntologyClassReader()
            obj.load_efo_classes()
            obj.rdf_graph = None
            self._set_in_pickled_file_cache(obj, cache_file)
        self.lookup.efo_ontology = obj


    def _get_available_publications(self):
        self.logger.info('getting literature/publications')
        self.lookup.available_publications = LiteratureLookUpTable(self.es, 'LITERATURE_LOOKUP', self.r_server)


    def _get_from_pickled_file_cache(self, file_id):
        file_path = os.path.join(Config.ONTOLOGY_CONFIG.get('pickle', 'cache_dir'), file_id+'.pck')
        if os.path.isfile(file_path):
            return pickle.load(open(file_path, 'rb'))

    def _set_in_pickled_file_cache(self, obj, file_id):
        if not os.path.isdir(os.path.join(Config.ONTOLOGY_CONFIG.get('pickle', 'cache_dir'))):
            os.makedirs(os.path.join(Config.ONTOLOGY_CONFIG.get('pickle', 'cache_dir')))
        file_path = os.path.join(Config.ONTOLOGY_CONFIG.get('pickle', 'cache_dir'), file_id+'.pck')
        pickle.dump(obj,
                    open(file_path, 'wb'),)

    def _get_available_chembl_mappings(self):
        chembl_handler = ChEMBLLookup()
        chembl_handler.get_molecules_from_evidence()
        all_molecules = set()
        for target, molecules in  chembl_handler.target2molecule.items():
            all_molecules = all_molecules|molecules
        all_molecules = list(all_molecules)
        query_batch_size = 100
        for i in range(0, len(all_molecules) + 1, query_batch_size):
            chembl_handler._populate_synonyms_for_molecule(all_molecules[i:i + query_batch_size])
        self.lookup.chembl = chembl_handler

    def _get_available_hpa(self):
        self.logger.info('getting expressions')
        self.lookup.available_hpa = HPALookUpTable(self.es, 'HPA_LOOKUP',
                                                   self.r_server)