[![Build Status](https://travis-ci.com/opentargets/data_pipeline.svg?branch=master)](https://travis-ci.com/opentargets/data_pipeline)

[![Docker Repository on Quay.io](https://quay.io/repository/opentargets/mrtarget/status "Docker Repository on Quay.io")](https://quay.io/repository/opentargets/mrtarget)

The code in this repository is used to process different data files that provide evidence for the target-disease associations in the [Open Targets Platform](https://www.targetvalidation.org). Documentation on how to use the platform can be found [here](https://docs.targetvalidation.org) and the evidence and association data dumps can be found [here](https://www.targetvalidation.org/downloads/data). Please contact support [at] argetvalidation.org for feedback.

---

## Summary of the pipeline

### Overview
The pipeline can be broken down in a number of steps, each of which can be run as a separate command. Each command typically reads data from one or more sources (such as a URL or local file, or Elasticsearch) and writes into one or more Elasticsearch indexes.

#### `--rea` Reactome
Downloads and processes information into a local index for performance.
#### `--hpa` Expression
Downloads and processes information into a local index for performance.
#### `--gen` Target
Downloads and processes information from various sources. Is built around a "plugin" structure. Constructs an Elasticsarch index containg most of the information about each Target within the platform.
It requires `--rea` reactome step.
Note: HGNC,Ensembl,Uniprot plugins should always be first, as they initialize the gene list used in other plugins.
Note: Chembl is required by the `--sea` step below.
#### `--efo` Disease
Downloads and processes the Experimental Factor Ontology, as well as Human Phenotype Ontology
and other sources. Constructs an Elasticsarch index containg the information about each Disease within the platform.
#### `--eco` Evidence Code
Downloads and processes the Evidence Code Ontology and Sequence Ontology.
#### `--val` Validation
Read in evidence strings (either from filesystem or URLs) and validate. The validation includes syntatic JSON schema validation, as well as ensuring that the disease and target are appropriate.
This step will also make some corrections to evidence, where appropriate. For example,replacing Uniprot protein identifiers with Ensembl gene identifiers.
It requires `--gen` target, `--efo` disease, and `--eco` evidence code steps.
It is expecting JSON matching schema [1.6.0](https://raw.githubusercontent.com/opentargets/json_schema/1.6.0/opentargets.json).
#### `--as` Associations
This step reads the valide evidence strings and calculates the appropriate assocations as well as calculated their scores.
It requires `--val` validation, and `--hpa` expression steps.
#### `--sea` Search
This step will create the index `${DATA_RELEASE_VERSION}_search-data` which is used for the search function in the platform.
It requires `--as` associations step.
#### `--ddr` Relationships
This step will compute the target-to-target and disease-to-disease relationships. 
It requires `--as` associations step.

---

## Installation instructions

### Preparation

Please note that several steps require large amounts of CPU and memory, particularly on the full data set. It is recommended to use a machine with at least 16 CPU and 100GB RAM for the pipeline, and allow for a wallclock runtime of 18 hours or more.

#### Elasticsearch

You should have Elasticsearch avaliable to be used the pipeline code. Note that Elasticsearch v6 is not currently supported by the pipeline, and therefore 5.6 is the latest version (as of writing). And Elasticsearch instance can be run using Docker containers. 

After deploying elasticsearch, you should check that you can query its API. Running `curl localhost:9200` should show something like:
```json
{
  "name": "xxxxx",
  "cluster_name": "elasticsearch",
  "cluster_uuid": "xxxxxxxxxx",
  "version": {
    "number": "x.x.x",
    "build_hash": "xxxxx",
    "build_date": "yyyy-mm-ddThh:mm:ss.sssZ",
    "build_snapshot": false,
    "lucene_version": "x.x.x"
  },
  "tagline": "You Know, for Search"
}
```

For more information on Elasticsearch, see https://www.elastic.co/guide/en/elasticsearch/reference/5.6/getting-started.html

Note: you may need to increase the default size of the write thread pool from 200 to a higher value (e.g. 1000). See https://www.elastic.co/guide/en/elasticsearch/reference/7.1/modules-threadpool.html

#### Kibana

Kibana is useful to browse the output/input of the various steps.

You can install Kibana in a variety of ways, including via [docker](https://www.elastic.co/guide/en/kibana/5.6/docker.html)

**Important:** Kibana version [must be compatible](https://www.elastic.co/support/matrix#show_compatibility) with Elasticsearch.

Once Kibana is installed and deployed, check that it is working by browsing to `http://localhost:5601`

### Configuration

The configuration of the pipeline can be spit into three aspects - Operations, Data, and Legacy

#### Operations
Here the execution parameters of the pipeline can be controlled. For example, the address of the Elasticsearch server, number of worker threads, etc.

It makes use of the [ConfigArgParse](https://pypi.org/project/ConfigArgParse/) library to allow these to be specified on the command line, environemnt varibale, or in a config file (in decreasing order of precendence). 

See the default `mrtarget.ops.yml` file for detailed comments describing the avaliable options, or use the `--help` command line argument.

#### Data
These options describe how the data is to be processed. They are described in a [YAML](https://yaml.org/) file that can be specified to operations. See the [OpenTargets docs](https://docs.targetvalidation.org/technical-pipeline/technical-notes) technical notes with the relevant file for each release. 

#### Elasticsearch
These options describe how Elasticsearch is to be configured. They are described in a [YAML](https://yaml.org/) file that can be specified to operations. Default settings are included
in the respository as they are specific to a particular version of the pipeline and are not expected to change substantially between releases.

### Execution

#### Docker and Docker-compose

If you have [docker](https://www.docker.com/) and [docker-compose](https://docs.docker.com/compose/) then you can start Elasticsearch and Kibana in the background with:

```sh
docker-compose up -d elasticsearch kibana
```

By default, these will be accessible on http://localhost:9200 and http://localhost:5601 for Elasticsearch and Kibana respectively.


You can execute the pipeline with a command like:

```sh
docker-compose run --rm mrtarget --dry-run
```

or:

```sh
docker-compose run --rm mrtarget --help
```

#### Using the Makefile

[Make](https://en.wikipedia.org/wiki/Make_(software)) is a venerable tool for automatically executing commands, including handling dependencies and partial updates. There is a Makefile which can be used to run all or parts of the pipeline, including checking for the existence of required indices. 

To use the makefile, customise the variables at the top of `Makefile` to suit your needs, then run

`make <target>`

Note that the variables can also be overridden on the command-line.

There are several targets, one for each stage of the pipeline, as well as composite targets, such as 

 * `all`
 * `base`
 * `validate_all`
 
 (see the actual Makefile for the full list, or the output of `make -r -R -p`)

Each target checks that the required Elasticsearch indices exist (via `scripts/check_index.py`) before execution.

There are several targets which speed up common tasks, such as 
 * `list_indices`
 * `clean` (see also `clean_json`, `clean_logs`, `clean_indices`)
 * `shell`
 * `dry_run`

##### Notes

*Shell completion*: most shells will complete the list of targets when `<TAB>` is pressed. This is a useful way of seeing which target(s) are available.

*Parallel execution*: `make -j` will run all the dependencies of a target in parallel. Useful for the `load_data` and `validate_all` stages. Using a value will limit to only that number of jobs e.g. `-j 4` will limit to 4. Using `-l x` will only create new jobs if the total load on the machine is below that threashold - usefuul as several of the stages themselves run over multiple processess. These can be combined - for example `make -j 8 -l 4` will spawn up to 8 jobs at the same time as long as the load is less than 4 when creating them. 

*Partial execution*: the targets inside the makefile use absolute paths. While this is useful for running the makefile from a directory outside of the root of the project,
when only a partial execution is desired (e.g. for testing) then the full path will be required.

*Recovery*: Make is designed around files, and regneerating them when out of date. To work with the OpenTargets pipeline, the files it is based on are the log files produced by the various stages. This means that if you need to rerun a stage (e.g. to regenerate an Elasticsearch index) you will need to delete the appropriate log file and re-run make. If a stage is cancelled, Make will automatically delete the logfile - which may not be what you want to happen!


##### Using the Makefile within Docker

It is possible to use both docker and the makefile together. You will need to override the default entrypoint of the docker image. For example:

```sh
docker-compose run --rm --entrypoint make mrtarget -j -l 8 -r -R -k all
```

As discussed above, by default, the docker compose file will *not use a locally built image*. See above for how to work with this.

##### Using Google Cloud Platform

There is a script `scripts/run_on_gcp.sh` that puts together the information above to create a virtual machine on Google Cloud Platform (GCP), install Docker and Docker Compose, and execute the pipeline via the Makefile within a Docker container. The only prerequisite is [Google Cloud SDK](https://cloud.google.com/sdk/docs/quickstarts) (gcloud) and then run `scripts/run_on_gcp.sh`.

This will run a tagged version, so if you want use something else or to make your own changes, then you'll need to do more in-depth investigation. Note, this will incur non-trivial costs on Google Cloud Platform; at current prices this may be around $25 USD, plus network and storage.

---

## Contributing

TODO: write me.

### Setting up on PyCharm Professional (on Linux)

The simplest way to ensure that the dependencies on your development machine match those in production is to have the project
interpreter be the same interpreter as will be used in production. 

This can be achieved by configuring the project to use a Docker container as the interpreter. In order to do this you need
to have Docker installed locally on your machine. 

1. Amend the `Dockerfile` so the final two lines are as follows:

```dockerfile
#ENTRYPOINT [ "scripts/entrypoint.sh" ]
CMD ["python3"]
``` 

2. Build the Docker image by executing the following command from the directory containing the Dockerfile: `docker build --tag data-pipeline-env .`

3. Clean up with `git checkout HEAD -- Dockerfile`

4. Go to 'Settings -> Project Interpreter' and then:
  - Select 'Add'
  - Select Docker from the options on the lefthand side
  - Select 'New' and then 'Unix Socket'. The installed Docker instance will be found and you will see a 'connection successful' message.
  - Select the image from the dropdown list from set 2. 
  
Now PyCharm will use an instance of the container when working on the data-pipeline so you can be sure that your development and production environments are the same.
 

### Development with docker-compose

Included in the repository is a `docker-compose.yml` file. This can be used by [docker-compose] (https://docs.docker.com/compose/) to orchestrate the relevant docker containers required to run the platform.

Docker-compose has the ability to layer multiple docker-compose.yml files together; by default, `docker-compose.override.yml` will be added to `docker-compose.yml`. This can be used to use an override to build the image locally i.e.:

```sh
docker-compose run --rm -f docker-compose.yml -f docker-compose.override.yml mrtarget --dry-run my-elasticsearch-prefix
```

This is done because overrides cannot remove previous values, so once a build directive has been specified it will always be used. Therefore, the build instruction must be outside of the default docker-compose.yml to support cases where the pipeline should be run but not built.

### Profiling

There is some additional configuration to build a docker container that can run the pipeline while profiling it with [PyFlame](https://github.com/uber/pyflame) to produce
output that [FlameGraph](https://github.com/brendangregg/FlameGraph) can turn into pretty interactive svg images.

To do this, use the `Dockerfile.pyfile` to build the container, and run it ensuring that the kernel of the host has `kernel.yama.ptrace_scope=0` and `--cap-add=SYS_PTRACE` on the docker container. See the updated entrypoint `scripts/entrypoint.pyflame.sh` for more details.

It will output to `logs/profile.*.svg` which can be opened with a browser e.g. Google Chrome. Note, while profiling performance may be slower.

There is an alternative profiler [py-spy](https://github.com/benfred/py-spy) and associated Dockerfile `Dockerfile.py-spy`.

### Other development tools

To identify potentially removable code, [Vulture](https://pypi.org/project/vulture/) and/or [Coverage](https://coverage.readthedocs.io/en/v4.5.x/) may be useful.

There is a simple script `scripts/entrypoint.time.sh` that wraps the pipline in GNU time with `-v` for verbose logging. This output includes peak memory consumption.


# Copyright

Copyright 2014-2019 Biogen, Celgene Corporation, EMBL - European Bioinformatics Institute, GlaxoSmithKline, Sanofi, Takeda Pharmaceutical Company and Wellcome Sanger Institute

This software was developed as part of the Open Targets project. For more information please see: http://www.opentargets.org

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either expressed or implied.
See the License for the specific language governing permissions and
limitations under the License.
