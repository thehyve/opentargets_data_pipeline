"""
Copyright 2014-2016 EMBL - European Bioinformatics Institute, Wellcome
Trust Sanger Institute, GlaxoSmithKline and Biogen

This software was developed as part of Open Targets. For more information please see:

	http://targetvalidation.org

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

	http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

.. module:: Ontology
    :platform: Unix, Linux
    :synopsis: A data pipeline module to extract information from ontologies.
.. moduleauthor:: Gautier Koscielny <gautierk@opentargets.org>
"""

import re
import sys
reload(sys);
sys.setdefaultencoding("utf8");
import os
import pysftp
import gzip
from paramiko import AuthenticationException
import opentargets.model.core as opentargets
import logging
import json
import rdflib
from rdflib import URIRef
from rdflib.namespace import Namespace, NamespaceManager
from rdflib.namespace import OWL, RDF, RDFS
from common import Actions
from SPARQLWrapper import SPARQLWrapper, JSON
from settings import Config
from tqdm import tqdm
from datetime import datetime, date
from settings import Config

__author__ = "Gautier Koscielny"
__copyright__ = "Copyright 2014-2016, Open Targets"
__credits__ = []
__license__ = "Apache 2.0"
__version__ = ""
__maintainer__ = "Gautier Koscielny"
__email__ = "gautierk@opentargets.org"
__status__ = "Production"

from logging.config import fileConfig

try:
    fileConfig(os.path.join(os.path.abspath(os.path.dirname(__file__)), '../logging_config.ini'))
except:
    pass
logger = logging.getLogger('Ontology')

TOP_LEVELS = '''
PREFIX obo: <http://purl.obolibrary.org/obo/>
select *
FROM <http://purl.obolibrary.org/obo/hp.owl>
FROM <http://purl.obolibrary.org/obo/mp.owl>
where {
  ?top_level rdfs:subClassOf <%s> .
  ?top_level rdfs:label ?top_level_label
}
'''

DIRECT_ANCESTORS = '''
# %s
PREFIX obo: <http://purl.obolibrary.org/obo/>
SELECT ?dist1 as ?distance ?y as ?ancestor ?ancestor_label ?x as ?direct_child ?direct_child_label
FROM <http://purl.obolibrary.org/obo/hp.owl>
FROM <http://purl.obolibrary.org/obo/mp.owl>
   WHERE
    {
       ?x rdfs:subClassOf ?y
       option(transitive, t_max(1), t_in(?x), t_out(?y), t_step("step_no") as ?dist1) .
       ?y rdfs:label ?ancestor_label .
       ?x rdfs:label ?direct_child_label .
       FILTER (?x = <%s>)
    }
order by ?dist1
'''

INDIRECT_ANCESTORS = '''
PREFIX obo: <http://purl.obolibrary.org/obo/>
SELECT ?dist1 as ?distance ?y as ?ancestor ?ancestor_label ?z as ?direct_child ?direct_child_label
FROM <http://purl.obolibrary.org/obo/hp.owl>
FROM <http://purl.obolibrary.org/obo/mp.owl>
   WHERE
    {
       ?x rdfs:subClassOf ?y
       option(transitive, t_max(20), t_in(?x), t_out(?y), t_step("step_no") as ?dist1) .
       ?y rdfs:label ?ancestor_label .
       ?z rdfs:subClassOf ?y .
       ?z rdfs:label ?direct_child_label .
       {SELECT ?z WHERE { ?x2 rdfs:subClassOf ?z option(transitive) FILTER (?x2 = <%s>) }}
       FILTER (?x = <%s>)
    }
order by ?dist1
'''

SPARQL_PATH_QUERY = '''
PREFIX efo: <http://www.ebi.ac.uk/efo/>
SELECT ?node_uri ?parent_uri ?parent_label ?dist ?path
FROM <http://www.ebi.ac.uk/efo/>
WHERE
  {
    {
      SELECT *
      WHERE
        {
          ?node_uri rdfs:subClassOf ?y .
          ?node_uri rdfs:label ?parent_label
        }
    }
    OPTION ( TRANSITIVE, t_min(1), t_in (?y), t_out (?node_uri), t_step (?y) as ?parent_uri, t_step ('step_no') as ?dist, t_step ('path_id') as ?path ) .
    FILTER ( ?y = efo:EFO_0000408 )
  }
'''

'''
PREFIX obo: <http://purl.obolibrary.org/obo/>

select ?superclass where {
  obo:HP_0003074 rdfs:subClassOf* ?superclass
}
'''

SINGLE_CLASS_PATH_QUERY = '''
PREFIX obo: <http://purl.obolibrary.org/obo/>

select ?class ?parent_label count(?mid) AS ?count
FROM <http://purl.obolibrary.org/obo/hp.owl>
where {
  obo:HP_0003074 rdfs:subClassOf* ?mid .
  ?mid rdfs:subClassOf* ?class .
  ?class rdfs:label ?parent_label .
}
group by ?class ?parent_label
order by ?count
'''

'''
subclass generators: yield a series of values
'''
def _get_subclass_of(arg, graph):
    logger.debug("[_get_subclass_of] %i"%len(arg))
    node = arg[0]
    depth = arg[1]
    path = arg[2]
    level = arg[3]
    logger.debug("Superclass: %s; label: %s; depth: %i"%(str(node.identifier), node.value(RDFS.label), depth))

    if level > 0 and depth == level:
        return
    for c in graph.subjects(predicate=RDFS.subClassOf, object=node.identifier):
        cr = rdflib.resource.Resource(graph, c)
        label = cr.value(RDFS.label)
        logger.debug("\tSubClass: %s; label: %s"%(str(cr.identifier), label))

        #synonyms = []
        # get synonyms by filtering on triples
        #for subject, predicate, obj in graph.triples(
        #        (c,
        #         rdflib.term.URIRef('http://www.geneontology.org/formats/oboInOwl#hasExactSynonym'),
        #         None)):
        #    print subject, predicate, obj
        # create the path here
        yield (cr, depth+1, path + (node,), level)

'''
superclass generators: yield a series of values
'''
def write_superclasses(arg, source_graph):
    print "[write_superclasses] %i"%len(arg)
    node = arg[0]
    destination_graph = arg[1]
    print node
    print node.qname()
    print node.value(RDFS.label)
    if (node, None, None) not in destination_graph:
        superclasses = list(node.transitive_objects(RDFS.subClassOf))
        for c in superclasses:
            if node.qname() != c.qname():
                print c
                label = c.value(RDFS.label)
                destination_graph.add((node, RDFS.subClassOf, c))
                yield (c, destination_graph)


class OntologyActions(Actions):
    PHENOTYPESLIM = 'phenotypeslim'
    DISEASEPHENOTYPES = 'diseasephenotypes'

class OntologyClassReader():

    def __init__(self):
        """Initialises the class

        Declares an RDF graph that will contain an ontology representation.
        Current classes are extracted in the current_classes dictionary
        Obsolete classes are extracted in the obsolete_classes dictionary
        """
        self.rdf_graph = rdflib.Graph()
        self.current_classes = dict()
        self.obsolete_classes = dict()
        self.top_level_classes = dict()
        self.classes_paths = dict()

    def load_ontology_graph(self, uri):
        """Loads the ontology from a URI in a RDFLib graph.

        Given a uri pointing to a OWL file, load the ontology representation in a graph.

        Args:
            uri (str): the URI for the ontology representation in OWL.

        Returns:
            None

        Raises:
            None

        """
        self.rdf_graph.parse(uri, format='xml')

    def load_ontology_classes(self, base_class=None):
        """Loads all current and obsolete classes from an ontology stored in RDFLib

        Given a base class in the ontology, extracts the classes and stores the sets of
        current and obsolete classes in dictionaries. This avoids traversing all the graph
        if only a few branches are required.

        Args:
            base_classes (list of str): the root(s) of the ontology to start from.

        Returns:
            None

        Raises:
            None

        """
        sparql_query = '''
        SELECT DISTINCT ?ont_node ?label
        {
        ?ont_node rdfs:subClassOf* <%s> .
        ?ont_node rdfs:label ?label
        }
        '''

        count = 0
        qres = self.rdf_graph.query(sparql_query % base_class)

        for row in qres:
            uri = str(row[0])
            label = str(row[1])
            self.current_classes[uri] = label
            count +=1
            logger.info("RDFLIB '%s' '%s'" % (uri, label))
        logger.debug("%i"%count)

        sparql_query = '''
        SELECT DISTINCT ?ont_node ?label ?id ?ont_new
         {
            ?ont_node owl:deprecated true .
            ?ont_node oboInOwl:id ?id .
            ?ont_node obo:IAO_0100001 ?ont_new_id .
            ?ont_new oboInOwl:id ?ont_new_id .
            ?ont_node rdfs:label ?label
         }
        '''

        qres = self.rdf_graph.query(sparql_query)
        obsoletes = dict()
        for row in qres:
            uri = str(row[0])
            label = str(row[1])
            id = str(row[2])
            new_uri = str(row[3])
            # point to the new URI
            obsoletes[uri] = { 'label': label, 'new_uri' : new_uri }
            count +=1
            logger.info("Obsolete %s '%s' %s" % (uri, label, new_uri))
        """
        Still need to loop over to find the next new class to replace the
        old URI with the latest URI (some intermediate classes can be obsolete too)
        """

        for old_uri in obsoletes.keys():
            next_uri = obsoletes[old_uri]['new_uri']
            while next_uri in obsoletes.keys():
                next_uri = obsoletes[next_uri]['new_uri']
            if next_uri in self.current_classes:
                new_label = self.current_classes[next_uri]
                logger.warn("%s => %s" % (old_uri, obsoletes[old_uri]))
                self.obsolete_classes[old_uri] = "Use %s label:%s" % (next_uri, new_label)
            else:
                self.obsolete_classes[old_uri] = "Obsolete class"

        sparql_query = '''
        select DISTINCT ?top_level ?top_level_label
        {
          ?top_level rdfs:subClassOf <%s> .
          ?top_level rdfs:label ?top_level_label
        }
        '''
        qres = self.rdf_graph.query(sparql_query % base_class)

        for row in qres:
            uri = str(row[0])
            label = str(row[1])
            self.top_level_classes[uri] = label
            logger.info("RDFLIB TOP LEVEL '%s' '%s'" % (uri, label))

        return

    def get_ancestors(self, id):
        """Return a list of ancestors"""
        return

    def get_classes_paths(self, uri, level=0):

        root_node = rdflib.resource.Resource(self.rdf_graph,
                                                   rdflib.term.URIRef(uri))

        logger.debug("* get_paths start")

        # create an entry for the root:
        root_node_uri = str(root_node.identifier)
        self.classes_paths[root_node_uri] = {'all': [], 'labels': [], 'ids': []}
        self.classes_paths[root_node_uri]['all'].append([{'uri': str(root_node_uri), 'label': root_node.value(RDFS.label)}])
        self.classes_paths[root_node_uri]['labels'].append([root_node.value(RDFS.label)])
        (prefix, namespace, id) = self.rdf_graph.namespace_manager.compute_qname(root_node.identifier)
        self.classes_paths[root_node_uri]['ids'].append([id])


        for entity in self.rdf_graph.transitiveClosure(_get_subclass_of, (root_node, 0, (), level)):

            node = entity[0]
            depth = entity[1]
            path = entity[2]

            node_uri = str(node.identifier)

            if node_uri not in self.classes_paths:
                self.classes_paths[node_uri] = { 'all': [], 'labels': [], 'ids': [] }

            all_struct = []
            labels_struct = []
            ids_struct = []

            for n in path:
                all_struct.append({'uri': str(n.identifier), 'label': n.value(RDFS.label)})
                labels_struct.append( n.value(RDFS.label) )
                (prefix, namespace, id) = self.rdf_graph.namespace_manager.compute_qname(n.identifier)
                ids_struct.append( id )

            all_struct.append( {'uri': str(node_uri), 'label': node.value(RDFS.label)} )
            labels_struct.append( node.value(RDFS.label) )
            (prefix, namespace, id) = self.rdf_graph.namespace_manager.compute_qname(node.identifier)
            ids_struct.append( id )

            self.classes_paths[node_uri]['all'].append(all_struct)
            self.classes_paths[node_uri]['labels'].append(labels_struct)
            self.classes_paths[node_uri]['ids'].append(ids_struct)

            logger.debug("** %s **"%node_uri)
            logger.debug( json.dumps(self.classes_paths[node_uri], indent=2) )

        logger.debug("* get_paths end")
        return

    def load_hpo_classes(self):
        """Loads the HPO graph and extracts the current and obsolete classes.
           Status: production
        """
        self.load_ontology_graph(Config.ONTOLOGY_CONFIG.get('uris', 'hpo'))
        base_class = 'http://purl.obolibrary.org/obo/HP_0000118'
        self.load_ontology_classes(base_class=base_class)

    def load_mp_classes(self):
        """Loads the HPO graph and extracts the current and obsolete classes.
           Status: production
        """
        self.load_ontology_graph(Config.ONTOLOGY_CONFIG.get('uris', 'mp'))
        base_class = 'http://purl.obolibrary.org/obo/MP_0000001'
        self.load_ontology_classes(base_class= base_class)

        #self.get_ontology_top_levels(base_class, top_level_map=self.phenotype_top_levels)


    def load_efo_classes(self):
        """Loads the EFO graph and extracts the current and obsolete classes.
           Status: production
        """
        self.load_ontology_graph(Config.ONTOLOGY_CONFIG.get('uris', 'efo'))
        # load disease, phenotype, measurement, biological process
        for base_class in [ 'http://www.ebi.ac.uk/efo/EFO_0000408', 'http://www.ebi.ac.uk/efo/EFO_0000651', 'http://www.ebi.ac.uk/efo/EFO_0001444', 'http://purl.obolibrary.org/obo/GO_0008150' ]:
            self.load_ontology_classes(base_class=base_class)

    def load_evidence_classes(self):

        self.load_ontology_graph('/Users/koscieln/Downloads/eco.owl')
        self.load_ontology_graph('/Users/koscieln/Downloads/so-xp.owl')

        evidence_uri = URIRef('http://purl.obolibrary.org/obo/ECO_0000000')

        for triple in self.rdf_graph.triples((evidence_uri, None, None)):
        #for triple in self.rdf_graph.triples((subject, RDFS.subClassOf, None)):
             logger.debug(triple)

        '''
            Open Targets specific evidence:
            A) Create namespace for OT evidence
            B) Add Open Targets evidence terms to graph
            C) Traverse the graph breadth first
        '''

        ot1 = Namespace(unicode("http://www.targetvalidation.org/disease"))
        ot2 = Namespace(unicode("http://identifiers.org/eco"))

        namespace_manager = NamespaceManager(self.rdf_graph)
        #self.rdf_graph.namespace_manager.bind('ot1', URIRef(ot1, override=False)
        #self.rdf_graph.namespace_manager.bind('ot2', ot2, override=False)
        self.rdf_graph.namespace_manager.bind('cttv', Namespace(unicode("http://www.targetvalidation.org/disease")))
        self.rdf_graph.namespace_manager.bind('ot', Namespace("http://identifiers.org/eco"))

        open_targets_terms = {
            'http://www.targetvalidation.org/disease/cttv_evidence':'CTTV evidence',
            'http://identifiers.org/eco/drug_disease':'drug-disease evidence',
            'http://identifiers.org/eco/target_drug':'biological target to drug evidence',
            'http://identifiers.org/eco/clinvar_gene_assignments':'ClinVAR SNP-gene pipeline',
            'http://identifiers.org/eco/cttv_mapping_pipeline':'CTTV-custom annotation pipeline',
            'http://identifiers.org/eco/GWAS_fine_mapping':'Fine-mapping study evidence',
            'http://www.targetvalidation.org/evidence/genomics_evidence':'genomics evidence'
        }

        for uri, label in open_targets_terms.iteritems():
            u = URIRef(uri)
            self.rdf_graph.add((u, RDF.type, rdflib.term.URIRef(u'http://www.w3.org/2002/07/owl#Class')))
            self.rdf_graph.add([u, RDFS.label, rdflib.Literal(label)])
            self.rdf_graph.add([u, RDFS.subClassOf, evidence_uri])

        u = URIRef('http://identifiers.org/eco/target_drug')
        for triple in self.rdf_graph.triples((u, None, None)):
             logger.debug(triple)

        #(a, b, c) = self.rdf_graph.namespace_manager.compute_qname(unicode('http://identifiers.org/eco/target_drug'))
        #logger.debug(c)
        #return
        # Add the bits specific to Open Targets
        # 'Open Targets evidence' ?
        # 'biological target-disease association via drug' ECO:0000360
        # 'drug-disease evidence' http://identifiers.org/eco/drug_disease SubclassOf 'biological target-disease association via drug'
        # 'biological target to drug evidence' http://identifiers.org/eco/target_drug SubclassOf 'biological target-disease association via drug'
        # ClinVAR SNP-gene pipeline http://identifiers.org/eco/clinvar_gene_assignments SubclassOf ECO:0000246
        # CTTV-custom annotation pipeline http://identifiers.org/eco/cttv_mapping_pipeline SubclassOf ECO:0000246

        #self.load_ontology_graph(Config.ONTOLOGY_CONFIG.get('uris', 'so'))
        #self.load_ontology_graph(Config.ONTOLOGY_CONFIG.get('uris', 'eco'))

        for base_class in ['http://purl.obolibrary.org/obo/ECO_0000000']: #, 'http://purl.obolibrary.org/obo/SO_0000400', 'http://purl.obolibrary.org/obo/SO_0001260', 'http://purl.obolibrary.org/obo/SO_0000110', 'http://purl.obolibrary.org/obo/SO_0001060' ]:
            self.load_ontology_classes(base_class=base_class)
            self.get_classes_paths(uri=base_class, level=0)

class DiseasePhenotypes():

    def __init__(self):
        self.rdf_graph = None
        pass

    def get_subclasses(self, node, node_lable):
        subclasses = list(node.transitive_subjects(RDFS.subClassOf))
        for c in subclasses:
            if node.qname() != c.qname():
                print c
                label = c.value(RDFS.label)
                synonyms = []
                # get synonyms by filtering on triples
                for subject, predicate, obj in self.rdf_graph.triples(
                        (rdflib.term.URIRef("http://purl.obolibrary.org/obo/SO_0001060"),
                         rdflib.term.URIRef('http://www.geneontology.org/formats/oboInOwl#hasExactSynonym'),
                         None)):
                    print subject, predicate, obj
                    synonyms.append(obj)

                print "%s -> %s (%s) synonyms:%s" %(node.qname(), c.qname(), label, ",".join(synonyms))
                self.get_subclasses(c, label)



    # https://sourceforge.net/p/efo/code/HEAD/tree/trunk/src/efoassociations/
    # https://sourceforge.net/p/efo/code/HEAD/tree/trunk/src/efoassociations/ibd_2_pheno_associations.owl?format=raw
    def parse_owl_url(self):
        self.get_eco_paths()

    def get_eco_paths(self):

        eco_paths = {}


        '''
        CTTV evidence => evidence
        "[{"uri": "http://www.targetvalidation.org/disease/cttv_evidence", "label": "CTTV evidence"}, {"uri": "http://purl.obolibrary.org/obo/ECO_0000360", "label": "biological target-disease association via drug"}, {"uri": "http://identifiers.org/eco/drug_disease" (...)"
        "[{"uri": "http://www.targetvalidation.org/disease/cttv_evidence", "label": "CTTV evidence"}, {"uri": "http://purl.obolibrary.org/obo/ECO_0000360", "label": "biological target-disease association via drug"}, {"uri": "http://identifiers.org/eco/target_drug", (...)"
            animal model system study evidence
            author statement
            "http://www.targetvalidation.org/evidence/literature_mining"
            similarity

        :return:
        '''

        # https://raw.githubusercontent.com/evidenceontology/evidenceontology/master/eco.owl
        query='''
        PREFIX obo: <http://purl.obolibrary.org/obo/>
        SELECT ?node_uri ?parent_uri sample(?parent_label) AS ?parent_label ?dist ?path
        WHERE
        {
            {
            SELECT *
            WHERE
            {
            ?node_uri rdfs:subClassOf ?y .
            ?node_uri rdfs:label ?parent_label
            }
        }
        OPTION ( TRANSITIVE, T_NO_CYCLES, T_DISTINCT, t_min(1), t_in (?y), t_out (?node_uri), t_step (?y) as ?parent_uri, t_step ('step_no') as ?dist, t_step ('path_id') as ?path ) .
        FILTER ( ?y = <http://www.targetvalidation.org/evidence/cttv_evidence> )
        }
        ORDER BY ?path ?dist
        '''

        self.rdf_graph = rdflib.Graph()
        print ("parse ECO")
        self.rdf_graph.parse('/Users/koscieln/Downloads/eco.owl', format='xml')
        self.rdf_graph.parse('/Users/koscieln/Downloads/so-xp.owl', format='xml')

        # assertion method
        subject = root = rdflib.term.URIRef("http://purl.obolibrary.org/obo/ECO_0000217")
        root_a = rdflib.resource.Resource(self.rdf_graph, subject)
        root_b = rdflib.resource.Resource(self.rdf_graph, rdflib.term.URIRef("http://purl.obolibrary.org/obo/ECO_0000000"))

        #sequence variant
        so_root_d = rdflib.resource.Resource(self.rdf_graph, rdflib.term.URIRef("http://purl.obolibrary.org/obo/SO_0001060"))


        print ("prepare query")
        print ("prepare query")
        for triple in self.rdf_graph.triples((subject, None, None)):
        #for triple in self.rdf_graph.triples((subject, RDFS.subClassOf, None)):
            print triple

        print ("Subclasses")
        subclasses = list(root_a.transitive_subjects(RDFS.subClassOf))
        for c in subclasses:
            print c
            print c.qname()

        label_a = root_a.value(RDFS.label)
        self.get_subclasses(root_a, label_a)

        print "-User-defined transitive closures"
        for tuple in self.rdf_graph.transitiveClosure(_get_subclass_of, (root_a, 0, (), 0)):
            print tuple
        print "-end of function"

        classes_path = dict()

        genomic_context = rdflib.resource.Resource(self.rdf_graph, rdflib.term.URIRef("http://purl.obolibrary.org/obo/ECO_0000177"))
        print "* User-defined transitive closures 2"
        for entity in self.rdf_graph.transitiveClosure(_get_subclass_of, (genomic_context, 0, (), 0)):
            node = entity[0]
            depth = entity[1]
            path = entity[2]
            if node not in classes_path:
                classes_path[node] = []
            js_struct = []
            for n in path:
                js_struct.append({'uri': str(n), 'label': n.value(RDFS.label)})
            js_struct.append({'uri': str(node), 'label': node.value(RDFS.label)})
            classes_path[node].append(js_struct)
            print json.dumps(js_struct, indent=2)

        print "* end of function 2"


        for triple in self.rdf_graph.triples((rdflib.term.URIRef("http://purl.obolibrary.org/obo/SO_0001060"), None, None)):
            print triple

        for triple in self.rdf_graph.triples(
                    (rdflib.term.URIRef("http://purl.obolibrary.org/obo/SO_0001060"),
                     rdflib.term.URIRef('http://www.geneontology.org/formats/oboInOwl#hasExactSynonym'),
                     None)):
            print triple
        #so_root_d_label = so_root_d.value(RDFS.label)
        #self.get_subclasses(so_root_d, so_root_d_label)

        #label_b = root_b.value(RDFS.label)
        #self.get_subclasses(root_b, label_b)

        #print ("")
        #for i in self.rdf_graph.subjects(RDFS.subClassOf, subject):
        #    yield i
        #for i in graph.subjects()
        #qres = self.rdf_graph.query(query)

        #for row in qres:
        #    print ("node_uri:%s parent_uri:%s parent_label:%s dist:%s path:%s" % row)

    def get_disease_phenotypes(self):
        # we care about namespaces
        #oban = Namespace('http://purl.org/oban/')
        self.rdf_graph = rdflib.Graph()
        # load HPO:
        print ("parse HPO")
        self.rdf_graph.parse(Config.ONTOLOGY_CONFIG.get('uris', 'hpo'), format='xml')
        print ("parse MP")
        self.rdf_graph.parse(Config.ONTOLOGY_CONFIG.get('uris', 'mp'), format='xml')
        #print ("parse EFO")
        #self.rdf_graph.parse('/Users/koscieln/Downloads/hp.owl', format='xml')
        for key, uri in Config.ONTOLOGY_CONFIG.items('disease_phenotypes_uris'):
            print ("merge phenotypes from %s" % uri)
            self.rdf_graph.parse(uri, format='xml')

        #graph.parse('https://sourceforge.net/p/efo/code/HEAD/tree/trunk/src/efoassociations/ibd_2_pheno_associations.owl?format=raw', format='xml')

        #for subject, predicate, obj in self.rdf_graph:
        #    print subject, predicate, obj
        #for stmt in graph:
        #    pprint.pprint(stmt)

        qres = self.rdf_graph.query(
            """
            PREFIX obo: <http://purl.obolibrary.org/obo/>
            PREFIX oban: <http://purl.org/oban/>
            select DISTINCT ?disease_id ?phenotype_label ?phenotype_id
            where {
              ?code oban:association_has_subject ?disease_id .
              ?code oban:association_has_object ?phenotype_id .
              ?phenotype_id rdfs:label ?phenotype_label
            }
            """
        )

        for row in qres:
            print ("%s hasPhenotype %s (%s)" % row)

        # EXTRACT THE GOOD BITS


class PhenotypeSlim():

    def __init__(self, sparql):

        self.sparql = sparql
        self.rdf_graph = rdflib.Graph()
        self.phenotypes = None

        self.phenotype_map = {}
        self.phenotype_excluded = set()

        self._remote_filenames = dict()
        self.logger = logging.getLogger(self.__class__.__name__)


    def get_ontology_path(self, base_class, term):

        """ if the term is already there or if it is a disease term """
        if term in self.phenotype_map:
            return


        #if term == 'http://purl.obolibrary.org/obo/HP_0001251':
        if True:

            for sparql_query in [DIRECT_ANCESTORS, INDIRECT_ANCESTORS]:
                self.sparql.setQuery(sparql_query%(term, term))
                self.sparql.setReturnFormat(JSON)
                results = None
                n = 0
                while (n < 2):
                    try:
                        results = self.sparql.query().convert()
                        n = 3
                    except SPARQLWrapper.SPARQLExceptions.EndPointNotFound, e:
                        print e
                        self.logger.error(e)
                        if n > 2:
                            raise e
                        else:
                            n=n+1

                #print len(results)
                #print json.dumps(results)

                for result in results["results"]["bindings"]:
                    #print json.dumps(result)
                    count = int(result['distance']['value'])
                    parent_label = result['ancestor_label']['value']
                    ancestor = result['ancestor']['value']
                    direct_child = result['direct_child']['value']
                    direct_child_label = result['direct_child_label']['value']
                    ''' Add the term to the phenotype map '''
                    if direct_child not in self.phenotype_map:
                        self.phenotype_map[direct_child] = { 'label': direct_child_label , 'superclasses': [] }
                    ''' Put all the ancestors to the phenotype map '''
                    if ancestor not in self.phenotype_map[direct_child]['superclasses']:
                        self.phenotype_map[direct_child]['superclasses'].append(ancestor)
                        print "%i %s %s (direct child is %s %s)"%(count, parent_label, ancestor, direct_child_label, direct_child)
                        print "---------"
                    #print "%i %s %s (direct child is %s %s)"%(count, parent_label, ancestor, direct_child_label, direct_child)


    def load_all_phenotypes(self):
        '''
        Load HPO and MP to accept phenotype terms that are not in EFO
        :return:
        '''
        self.phenotypes = OntologyClassReader()
        self.phenotypes.load_hpo_classes()
        self.phenotypes.load_mp_classes()

    def exclude_phenotypes(self, l):
        '''
        :param l:
        :return:
        '''
        for p in l:
            if p not in self.phenotype_excluded:
                self.phenotype_excluded.add(p)
                print "Excluding %s"%p
                # get parents
                sparql_query = DIRECT_ANCESTORS
                self.sparql.setQuery(sparql_query%(p, p))
                self.sparql.setReturnFormat(JSON)
                results = self.sparql.query().convert()
                al = []
                for result in results["results"]["bindings"]:
                    count = int(result['distance']['value'])
                    parent_label = result['ancestor_label']['value']
                    ancestor = result['ancestor']['value']
                    al.append(ancestor)
                    self.exclude_phenotypes(al)


    def get_ontology_path2(self, base_class, id):

        resource_id = rdflib.resource.Resource(self.phenotypes.rdf_graph, rdflib.term.URIRef(id))
        print "* get_ontology_path2"
        for entity in self.rdf_graph.transitiveClosure(write_superclasses, (resource_id, self.rdf_graph)):
            print entity

    def generate_ttl_query(self, filename):

        with open(filename, 'w') as hfile:
            # create restricted list
            print ",".join(self.phenotype_top_levels.keys())
            for p in self.phenotype_top_levels:
                if p in self.phenotype_map:
                    self.exclude_phenotypes(self.phenotype_map[p]['superclasses'])
            # return

            hfile.write("@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n\n")

            for k, v in self.phenotype_map.iteritems():
                count = 0
                if k not in self.phenotype_excluded:
                    hfile.write("<%s> rdfs:label \"%s\" .\n" % (k, v['label']))
                    if k in self.phenotype_top_levels:
                        hfile.write("<%s> rdfs:subClassOf <http://www.ebi.ac.uk/efo/EFO_0000651> .\n" % k)
                    else:
                        for p in v['superclasses']:
                            hfile.write("<%s> rdfs:subClassOf <%s> .\n" % (k, p))

        hfile.close()

    def _store_remote_filename(self, filename):
        print "%s" % filename
        self.logger.debug("%s" % filename)
        if filename.startswith('/upload/submissions/') and \
            filename.endswith('.json.gz'):
            self.logger.debug("%s" % filename)
            if True:
                version_name = filename.split('/')[3].split('.')[0]
                print "%s" % filename
                if '-' in version_name:
                    user, day, month, year = version_name.split('-')
                    if '_' in user:
                        datasource = ''.join(user.split('_')[1:])
                        user = user.split('_')[0]
                    else:
                        datasource = Config.DATASOURCE_INTERNAL_NAME_TRANSLATION_REVERSED[user]
                    release_date = date(int(year), int(month), int(day))

                    if user not in self._remote_filenames:
                        self._remote_filenames[user]={datasource : dict(date = release_date,
                                                                          file_path = filename,
                                                                          file_version = version_name)
                                                      }
                    elif datasource not in self._remote_filenames[user]:
                        self._remote_filenames[user][datasource] = dict(date=release_date,
                                                                        file_path=filename,
                                                                        file_version=version_name)
                    else:
                        if release_date > self._remote_filenames[user][datasource]['date']:
                            self._remote_filenames[user][datasource] = dict(date=release_date,
                                                                file_path=filename,
                                                                file_version=version_name)
            #except e:
            #    self.logger.error("%s Error checking file %s: %s" % (self.__class__.__name__, filename, e))
            #    print 'error getting remote file%s'%filename
            #    self.logger.debug('error getting remote file%s'%filename)

    def _callback_not_used(self, path):
        self.logger.debug("skipped "+path)

    def create_phenotype_slim(self, local_files = []):

        self.load_all_phenotypes()

        if local_files:

            for file_path in local_files:
                self.logger.info("Parsing file %s" % (file_path))
                file_size, file_mod_time = os.path.getsize(file_path), os.path.getmtime(file_path)
                with open(file_path, mode='rb') as f:
                    self.parse_gzipfile(filename=file_path, mode='rb', fileobj=f, mtime=file_mod_time)
        else:

            for u in tqdm(Config.ONTOLOGY_PREPROCESSING_FTP_ACCOUNTS,
                             desc='scanning ftp accounts',
                             leave=False):
                try:
                    p = Config.EVIDENCEVALIDATION_FTP_ACCOUNTS[u]
                    self.logger.info("%s %s"%(u, p))
                    cnopts = pysftp.CnOpts()
                    cnopts.hostkeys = None  # disable host key checking.
                    with pysftp.Connection(host=Config.EVIDENCEVALIDATION_FTP_HOST['host'],
                                           port=Config.EVIDENCEVALIDATION_FTP_HOST['port'],
                                           username=u,
                                           password=p,
                                           cnopts = cnopts,
                                           ) as srv:
                        srv.walktree('/', fcallback=self._store_remote_filename, dcallback=self._callback_not_used, ucallback=self._callback_not_used)
                        srv.close()
                        for datasource, file_data in tqdm(self._remote_filenames[u].items(),
                                                          desc='scanning available datasource for account %s'%u,
                                                          leave=False,):
                            latest_file = file_data['file_path']
                            file_version = file_data['file_version']
                            self.logger.info("found latest file %s for datasource %s"%(latest_file, datasource))
                            self.parse_gzipfile(latest_file, u, p)
                except AuthenticationException:
                    self.logger.error('cannot connect with credentials: user:%s password:%s' % (u, p))

        self.generate_ttl_query(Config.ONTOLOGY_SLIM_FILE)

    def parse_gzipfile(self, filename, mode, fileobj, mtime):

        with gzip.GzipFile(filename=filename,
                           mode=mode,
                           fileobj=fileobj,
                           mtime=mtime) as fh:

            logging.info('Starting parsing %s' % filename)

            line_buffer = []
            offset = 0
            chunk = 1
            line_number = 0

            for line in fh:
                python_raw = json.loads(line)
                obj = None
                data_type = python_raw['type']
                if data_type in Config.EVIDENCEVALIDATION_DATATYPES:
                    if data_type == 'genetic_association':
                        obj = opentargets.Genetics.fromMap(python_raw)
                    elif data_type == 'rna_expression':
                        obj = opentargets.Expression.fromMap(python_raw)
                    elif data_type in ['genetic_literature', 'affected_pathway', 'somatic_mutation']:
                        obj = opentargets.Literature_Curated.fromMap(python_raw)
                        if data_type == 'somatic_mutation' and not isinstance(python_raw['evidence']['known_mutations'],
                                                                              list):
                            mutations = copy.deepcopy(python_raw['evidence']['known_mutations'])
                            python_raw['evidence']['known_mutations'] = [mutations]
                            # self.logger.error(json.dumps(python_raw['evidence']['known_mutations'], indent=4))
                            obj = opentargets.Literature_Curated.fromMap(python_raw)
                    elif data_type == 'known_drug':
                        obj = opentargets.Drug.fromMap(python_raw)
                    elif data_type == 'literature':
                        obj = opentargets.Literature_Mining.fromMap(python_raw)
                    elif data_type == 'animal_model':
                        obj = opentargets.Animal_Models.fromMap(python_raw)

                if obj.disease.id:
                    for id in obj.disease.id:
                        if re.match('http://purl.obolibrary.org/obo/HP_\d+', id):
                            ''' get all terms '''
                            self.get_ontology_path2('http://purl.obolibrary.org/obo/HP_0000118', id)

                        elif re.match('http://purl.obolibrary.org/obo/MP_\d+', id):
                            ''' get all terms '''
                            self.get_ontology_path2('http://purl.obolibrary.org/obo/MP_0000001', id)
                        elif re.match('http://www.orpha.net/ORDO/Orphanet_\d+', id):
                            ''' just map to the genetic disorders '''
                            self.get_ontology_path('http://www.ebi.ac.uk/efo/EFO_0000508', id)

        fh.close()

    def parse_sftp_gzipfile(self, file_path, u, p):
        print "---->%s"%file_path
        self.logger.info("%s %s" % (u, p))
        cnopts = pysftp.CnOpts()
        cnopts.hostkeys = None  # disable host key checking.
        with pysftp.Connection(host=Config.EVIDENCEVALIDATION_FTP_HOST['host'],
                               port=Config.EVIDENCEVALIDATION_FTP_HOST['port'],
                               username=u,
                               password=p,
                               cnopts=cnopts,
                               ) as srv:

            file_stat = srv.stat(file_path)
            file_size, file_mod_time = file_stat.st_size, file_stat.st_mtime

            with srv.open(file_path, mode='rb', bufsize=1) as f:
                self.parse_gzipfile(filename=file_path.split('/')[1], mode='rb', filepbj=f, mtime=file_mod_time)
            srv.close()
        return

def main():
    obj = DiseasePhenotypes()
    obj.parse_owl_url()

if __name__ == "__main__":
    main()