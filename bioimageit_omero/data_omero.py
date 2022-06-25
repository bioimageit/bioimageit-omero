# -*- coding: utf-8 -*-
"""BioImageIT OMERO metadata service.

This module implements the OMERO service for metadata
(Data, DataSet and Experiment) management.
This OMERO service read/write and query metadata from an OMERO database

Classes
------- 
OmeroMetadataServiceBuilder
OmeroMetadataService

"""
import numpy as np
import os
import os.path
import json
import re
from datetime import datetime

from skimage.io import imread, imsave
from omero.gateway import BlitzGateway, DatasetWrapper, ProjectWrapper
import omero

from omero.cli import cli_login
from bioimageit_formats import FormatsAccess, formatsServices

from bioimageit_core.core.config import ConfigAccess
from bioimageit_core.core.exceptions import DataServiceError
from bioimageit_core.containers.data_containers import (METADATA_TYPE_RAW,
                                                        METADATA_TYPE_PROCESSED,
                                                        Container,
                                                        RawData,
                                                        ProcessedData,
                                                        ProcessedDataInputContainer,
                                                        Dataset,
                                                        Experiment,
                                                        Run,
                                                        RunInputContainer,
                                                        RunParameterContainer,
                                                        DatasetInfo,
                                                        )
import argparse
import locale
import os
import platform
import sys

import omero.clients
from omero.cli import cli_login
from omero.model import ChecksumAlgorithmI
from omero.model import NamedValue
from omero.model.enums import ChecksumAlgorithmSHA1160
from omero.rtypes import rstring, rbool
from omero_version import omero_version
from omero.callbacks import CmdCallbackI
from omero.model import enums as omero_enums
import dask.array as da
from dask import delayed


PIXEL_TYPES = {
    omero_enums.PixelsTypeint8: np.int8,
    omero_enums.PixelsTypeuint8: np.uint8,
    omero_enums.PixelsTypeint16: np.int16,
    omero_enums.PixelsTypeuint16: np.uint16,
    omero_enums.PixelsTypeint32: np.int32,
    omero_enums.PixelsTypeuint32: np.uint32,
    omero_enums.PixelsTypefloat: np.float32,
    omero_enums.PixelsTypedouble: np.float64,
}

##################### Test ######################

def get_files_for_fileset(fs_path):
    if os.path.isfile(fs_path):
        files = [fs_path]
    else:
        files = [os.path.join(fs_path, f)
                 for f in os.listdir(fs_path) if not f.startswith('.')]
    return files


def create_fileset(files):
    """Create a new Fileset from local files."""
    fileset = omero.model.FilesetI()
    for f in files:
        entry = omero.model.FilesetEntryI()
        entry.setClientPath(rstring(f))
        fileset.addFilesetEntry(entry)

    # Fill version info
    system, node, release, version, machine, processor = platform.uname()

    client_version_info = [
        NamedValue('omero.version', omero_version),
        NamedValue('os.name', system),
        NamedValue('os.version', release),
        NamedValue('os.architecture', machine)
    ]
    try:
        client_version_info.append(
            NamedValue('locale', locale.getdefaultlocale()[0]))
    except:
        pass

    upload = omero.model.UploadJobI()
    upload.setVersionInfo(client_version_info)
    fileset.linkJob(upload)
    return fileset

def create_settings():
    """Create ImportSettings and set some values."""
    settings = omero.grid.ImportSettings()
    settings.doThumbnails = rbool(True)
    settings.noStatsInfo = rbool(False)
    settings.userSpecifiedTarget = None
    settings.userSpecifiedName = None
    settings.userSpecifiedDescription = None
    settings.userSpecifiedAnnotationList = None
    settings.userSpecifiedPixels = None
    settings.checksumAlgorithm = ChecksumAlgorithmI()
    s = rstring(ChecksumAlgorithmSHA1160)
    settings.checksumAlgorithm.value = s
    return settings


def upload_files(proc, files, client):
    """Upload files to OMERO from local filesystem."""
    ret_val = []
    for i, fobj in enumerate(files):
        rfs = proc.getUploader(i)
        try:
            with open(fobj, 'rb') as f:
                print ('Uploading: %s' % fobj)
                offset = 0
                block = []
                rfs.write(block, offset, len(block))  # Touch
                while True:
                    block = f.read(1000 * 1000)
                    if not block:
                        break
                    rfs.write(block, offset, len(block))
                    offset += len(block)
                ret_val.append(client.sha1(fobj))
        finally:
            rfs.close()
    return ret_val


def assert_import(client, proc, files, wait):
    """Wait and check that we imported an image."""
    hashes = upload_files(proc, files, client)
    print ('Hashes:\n  %s' % '\n  '.join(hashes))
    handle = proc.verifyUpload(hashes)
    cb = CmdCallbackI(client, handle)

    # https://github.com/openmicroscopy/openmicroscopy/blob/v5.4.9/components/blitz/src/ome/formats/importer/ImportLibrary.java#L631
    if wait == 0:
        cb.close(False)
        return None
    if wait < 0:
        while not cb.block(2000):
            sys.stdout.write('.')
            sys.stdout.flush()
        sys.stdout.write('\n')
    else:
        cb.loop(wait, 1000)
    rsp = cb.getResponse()
    if isinstance(rsp, omero.cmd.ERR):
        raise Exception(rsp)
    assert len(rsp.pixels) > 0
    return rsp


def full_import(client, fs_path, wait=-1):
    """Re-usable method for a basic import."""
    mrepo = client.getManagedRepository()
    files = get_files_for_fileset(fs_path)
    assert files, 'No files found: %s' % fs_path

    fileset = create_fileset(files)
    settings = create_settings()

    proc = mrepo.importFileset(fileset, settings)
    try:
        return assert_import(client, proc, files, wait)
    finally:
        proc.close()

def main_import(data_path, host, port, username, password):
    client=omero.client(host,port)
    session=client.createSession(username,password)
    conn = BlitzGateway(client_obj=client)

    print ('Importing: %s' % data_path)
    rsp = full_import(client, data_path)
    if rsp:
        links = []
        for p in rsp.pixels:
            print ('Imported Image ID: %d' % p.image.id.val)
            # if args.dataset:
            #     link = omero.model.DatasetImageLinkI()
            #     link.parent = omero.model.DatasetI(args.dataset, False)
            #     link.child = omero.model.ImageI(p.image.id.val, False)
            #     links.append(link)
        conn.getUpdateService().saveArray(links, conn.SERVICE_OPTS)
    
    return p.image.id.val


#################################################

plugin_info = {
    'name': 'OMERO',
    'type': 'data',
    'builder': 'OmeroMetadataServiceBuilder'
}


class OmeroMetadataServiceBuilder:
    """Service builder for the metadata service"""

    def __init__(self):
        self._instance = None

    def __call__(self, host, port, username, password):
        if not self._instance:
            self._instance = OmeroMetadataService(host, port, username, password)
        return self._instance


class OmeroMetadataService:
    """Service for local metadata management"""

    def __init__(self, host, port, username, password):
        self.service_name = 'OmeroMetadataService'
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._conn = BlitzGateway(self._username, self._password,
                                 host=self._host, port=self._port,
                                 secure=True)
        self._omero_connect()                         

    def __del__(self):
        if self._conn is not None:
            self._conn.close()             

    @staticmethod
    def _read_json(md_uri: str):
        """Read the metadata from the a json file"""
        if os.path.getsize(md_uri) > 0:
            with open(md_uri) as json_file:
                return json.load(json_file)

    @staticmethod
    def _write_json(metadata: dict, md_uri: str):
        """Write the metadata to the a json file"""
        with open(md_uri, 'w') as outfile:
            json.dump(metadata, outfile, indent=4)
 
    def _omero_connect(self):
        print('Omero connect')
        rv = self._conn.connect()
        if not rv:
            raise DataServiceError(
                'Unable to connect to the Omero database'
            )

    def _omero_close(self):
        print('Omero close')
        self._conn.close()
        print('Omero close done')

    def _omero_is_project(self, name):
        """Check is a project 

        Parameters
        ----------
        name: str
            Name of the project

        Returns
        -------
        True if a project with this name exists, False otherwise

        """
        value = False
        #self._omero_connect()
        try:
            projects = self._conn.getObjects("Project")
            for project in projects:
                if project.name == name:
                    value = True
        finally: 
            #self._omero_close()
            return value     

    def _omero_write_tiff_image(self, data_path, image_name, parent_dataset):
        numpy_image = [imread(data_path)]
        # TODO manage other image sizes
        size_z = 1
        size_c = 1
        size_t = 1

        def plane_gen():
            """generator will yield planes"""
            for p in numpy_image:
                yield p

        i = self._conn.createImageFromNumpySeq(plane_gen(), image_name, 
                                              size_z, size_c, size_t, 
                                              description='',
                                              dataset=parent_dataset)  

    def needs_cleanning(self):
        return True

    def create_experiment(self, name, author, date='now', keys=None,
                          destination=''):
        """Create a new experiment

        Parameters
        ----------
        name: str
            Name of the experiment
        author: str
            username of the experiment author
        date: str
            Creation date of the experiment
        keys: list
            List of keys used for the experiment vocabulary
        destination: str
            Destination where the experiment is created. It is a the path of the
            directory where the experiment will be created for local use case

        Returns
        -------
        Experiment container with the experiment metadata

        """
        if keys is None:
            keys = []
        container = Experiment()
        container.uuid = None
        container.name = name
        container.author = author
        container.date = date
        container.keys = keys

        #self._omero_connect()
        try:
            # check if the experiment already exists
            projects = self._conn.getObjects("Project")
            for project in projects:
                if project.name == name:
                    raise DataServiceError('Cannot create Experiment: a project with the same name '
                                           'already already exists in the Omero database')

            # create the project    
            new_project = ProjectWrapper(self._conn, omero.model.ProjectI())
            new_project.setName(name)
            new_project.setDescription('')
            new_project.save()
            project_obj = new_project._obj  
            container.uuid = project_obj.id
            container.md_uri = project_obj.id

            # create an empty raw dataset
            new_dataset = DatasetWrapper(self._conn, omero.model.DatasetI())
            new_dataset.setName('data')
            new_dataset.save()
            dataset_obj = new_dataset._obj
            container.raw_dataset = DatasetInfo('data', dataset_obj.id.val, dataset_obj.id.val)
        
            # link dataset to project
            link = omero.model.ProjectDatasetLinkI()
            link.setChild(omero.model.DatasetI(dataset_obj.id.val, False))
            link.setParent(omero.model.ProjectI(project_obj.id.val, False))
            self._conn.getUpdateService().saveObject(link)                        

            # add keys as Omero tags
            for tag in keys:
                tag_ann = omero.gateway.TagAnnotationWrapper(self._conn)
                tag_ann.setValue(tag)
                tag_ann.save()
                project_obj.linkAnnotation(tag_ann)
        finally: 
            #self._omero_close() 
            pass     
        return container

    def get_workspace_experiments(self, workspace_uri = ''):
        """Read the experiments in the user workspace

        Parameters
        ----------
        workspace_uri: str
            URI of the workspace

        Returns
        -------
        list of experiment containers  
          
        """
        #self._omero_connect()
        experiments = []
        try:
            projects = self._conn.getObjects("Project")
            for project in projects:
                container = Experiment()
                container.uuid = project.id
                container.name = project.name
                container.author = project.getDetails().getOwner().getOmeName()
                container.date = str(project.getDate())
                container.keys = {}
                experiments.append({'md_uri': project.id, 'info': container})
        finally: 
            #self._omero_close() 
            pass
        return experiments    

    def get_experiment(self, md_uri):
        """Read an experiment from the database

        Parameters
        ----------
        md_uri: str
            URI of the experiment. For local use case, the URI is either the
            path of the experiment directory, or the path of the
            experiment.md.json file

        Returns
        -------
        Experiment container with the experiment metadata

        """
        #self._omero_connect()
        container = Experiment()
        try:
            project = self._conn.getObject("Project", md_uri)
            if project is None:
                raise DataServiceError('Cannot find the experiment metadata from the given URI')
            container.uuid = project.id    
            container.md_uri = project.id
            container.name = project.name
            container.author = project.getDetails().getOwner().getOmeName()
            container.date = project.getDetails().getCreationEvent().getTime()
            # get the tags
            for ann in project.listAnnotations():
                if ann.OMERO_TYPE == omero.model.TagAnnotationI:
                    container.keys.append(ann.getValue())

            # get all the datasets
            for dataset in project.listChildren():
                if dataset.name == 'data':
                    container.raw_dataset = DatasetInfo(dataset.name,
                                                        dataset.id,
                                                        dataset.id)
                else:
                    container.processed_datasets.append(DatasetInfo(dataset.name,
                                                                    dataset.id,
                                                                    dataset.id)
                                                        )
        finally: 
            #self._omero_close() 
            pass
        return container  

    def update_experiment(self, experiment):
        """Write an experiment to the database

        Parameters
        ----------
        experiment: Experiment
            Container of the experiment metadata

        """
        #self._omero_connect()
        try:
            # set the main info
            project = self._conn.getObject("Project", experiment.md_uri)
            project.name = experiment.name
            #project.owner = experiment.author
            #project.date = experiment.date
            project.save()
            # set the tags
            # delete tags in the OMERO database and not in the keys list
            to_delete = []
            existing_tags = []
            for ann in project.listAnnotations():
                if ann.OMERO_TYPE == omero.model.TagAnnotationI and ann.getValue() not in experiment.keys:
                    to_delete.append(ann.id)
                else:
                    existing_tags.append(ann.getValue())    
            if len(to_delete) > 0:        
                self._conn.deleteObjects('Annotation', to_delete, wait=True)
            # add not existing tags
            for key in experiment.keys:
                if key not in existing_tags:
                    tag_ann = omero.gateway.TagAnnotationWrapper(self._conn)
                    tag_ann.setValue(key)
                    tag_ann.save()
                    project.linkAnnotation(tag_ann)
        finally:
            #self._omero_close()
            pass

    def import_data(self, experiment, data_path, name, author, format_,
                    date='now', key_value_pairs=dict()):
        """import one data to the experiment

        The data is imported to the raw dataset

        Parameters
        ----------
        experiment: Experiment
            Container of the experiment metadata
        data_path: str
            Path of the accessible data on your local computer
        name: str
            Name of the data
        author: str
            Person who created the data
        format_: str
            Format of the data (ex: tif)
        date: str
            Date when the data where created
        key_value_pairs: dict
            Dictionary {key:value, key:value} to annotate files

        Returns
        -------
        class RawData containing the metadata

        """
        #self._omero_connect()
        try:
            # get the raw dataset
            raw_dataset_id = experiment.raw_dataset.url
            dataset = self._conn.getObject("Dataset", raw_dataset_id)

            # copy the image to omero
            image_id = 0
            if format_ == 'imagetiff':
                image_id = main_import(data_path, self._host,self._port,self._username,self._password)
                link = omero.model.DatasetImageLinkI()
                link.setParent(omero.model.DatasetI(raw_dataset_id, False))
                link.setChild(omero.model.ImageI(image_id, False))
                self._conn.getUpdateService().saveObject(link)

            else:
                raise DataServiceError(f'OMERO service can only import tiff images (format={format_})')  

            # add key value pairs
            keys_value_list = []
            print(type(key_value_pairs))
            print(key_value_pairs)
            if len(key_value_pairs)!=0:
                for key, value in key_value_pairs.items():
                    keys_value_list.append([key, value])
                if len(keys_value_list) > 0:
                    map_ann = omero.gateway.MapAnnotationWrapper(self._conn)
                    namespace = omero.constants.metadata.NSCLIENTMAPANNOTATION
                    map_ann.setNs(namespace)
                    map_ann.setValue(keys_value_list)
                    map_ann.save()
                    image = self._conn.getObject("Image", image_id)
                    image.linkAnnotation(map_ann)      
        finally:
            #self._omero_close()
            pass    

        # create the container
        metadata = RawData()
        metadata.uuid = image_id
        metadata.md_uri = image_id
        metadata.name = name
        metadata.author = author
        metadata.format = format_
        metadata.date = date
        metadata.key_value_pairs = key_value_pairs

        return metadata

    def import_dir(self, experiment, dir_uri, filter_, author, format_, date,
                   directory_tag_key='', observers=None):
        """Import data from a directory to the experiment

        This method import with or without copy data contained
        in a local folder into an experiment. Imported data are
        considered as RawData for the experiment

        Parameters
        ----------
        experiment: Experiment
            Container of the experiment metadata
        dir_uri: str
            URI of the directory containing the data to be imported
        filter_: str
            Regular expression to filter which files in the folder
            to import
        author: str
            Name of the person who created the data
        format_: str
            Format of the image (ex: tif)
        date: str
            Date when the data where created
        directory_tag_key
            If the string directory_tag_key is not empty, a new tag key entry with the
            key={directory_tag_key} and the value={the directory name}.
        observers: list
            List of observers to notify the progress

        """
        files = os.listdir(dir_uri)
        count = 0
        key_value_pairs = {}
        if directory_tag_key != '':
            key_value_pairs[directory_tag_key] = os.path.dirname(dir_uri)

        if format_ == 'imagetiff':
            for file in files:
                count += 1
                r1 = re.compile(filter_)
                if r1.search(file):
                    if observers is not None:
                        for obs in observers:
                            obs.notify_progress(int(100 * count / len(files)), file)
                    self.import_data(experiment, os.path.join(dir_uri, file), file, author,
                                     format_, date, key_value_pairs)
        else:
            raise DataServiceError(f'OMERO service can only import tiff images (format={format_})')                                 

    def get_raw_data(self, md_uri):
        """Read a raw data from the database

        Parameters
        ----------
        md_uri: str
            URI if the raw data
        Returns
        -------
        RawData object containing the raw data metadata

        """
        container = RawData()
        #self._omero_connect()
        try:
            # read base info
            image = self._conn.getObject("Image", md_uri)
            container.uuid = md_uri
            container.md_uri = md_uri
            container.uri = md_uri
            container.type = 'raw'
            container.name = image.name
            container.author = image.getDetails().getOwner().getOmeName()
            time_stamp = image.getDetails().getCreationEvent().getTime()
            container.date = datetime.fromtimestamp(time_stamp/1000).strftime('%Y-%m-%d')
            container.format = 'imagetiff'

            # read metadata
            # TODO 

            # read key-value pairs
            for ann in image.listAnnotations():
                if ann.OMERO_TYPE == omero.model.MapAnnotationI:
                    values = ann.getValue()
                    for value in values:
                        container.key_value_pairs[value[0]] = value[1] 
            # read experiment tags if not in key_value_pairs
            project = self._conn.getObject('Project', image.getParent().getParent().id)
            for ann in project.listAnnotations():
                if ann.OMERO_TYPE == omero.model.TagAnnotationI:
                    if ann.getValue() not in container.key_value_pairs.keys():
                        container.key_value_pairs[ann.getValue()] = ''           
        finally:
            #self._omero_close()
            pass
        return container

    def update_raw_data(self, raw_data):
        """Read a raw data from the database

        Parameters
        ----------
        raw_data: RawData
            Container with the raw data metadata

        """

        #self._omero_connect()
        try:
            image = self._conn.getObject("Image", raw_data.md_uri)     
            image.name = raw_data.name 
            #image.owner = raw_data.author  
            image.date = raw_data.date 
            #image.format = raw_data.format
            image.save()

            # set the key value pairs
            # delete key in the OMERO database and not in the data keys list
            to_delete = []
            existing_tags = []
            keys = raw_data.key_value_pairs.keys()
            for ann in image.listAnnotations():
                if ann.OMERO_TYPE == omero.model.MapAnnotationI and ann.getValue()[0] not in keys:
                    to_delete.append(ann.id)
                else:
                    key_ = ann.getValue()[0]
                    ann.setValue([[key_, raw_data.key_value_pairs[key_]]])
                    existing_tags.append(ann.getValue()[0])   
            if len(to_delete) > 0:         
                self._conn.deleteObjects('Annotation', to_delete, wait=True)
            # add not existing tags
            for key in keys:
                if key not in existing_tags:
                    map_ann = omero.gateway.MapAnnotationWrapper(self._conn)
                    map_ann.setValue([[key, raw_data.key_value_pairs[key]]])
                    map_ann.save()
                    image.linkAnnotation(map_ann)
        finally:
            #self._omero_close()
            pass

    def _omero_download_image_md_attachments(self, image, destination_path):
        """Set an attachment file to an image

        Parameters
        ----------
        image: omero.Image
            Omero image container
        destination_path: str
            Path where the file is downloaded    

        """
        md_found = False
        for ann in image.listAnnotations():
            if isinstance(ann, omero.gateway.FileAnnotationWrapper):
                print("File ID:", ann.getFile().getId(), ann.getFile().getName(), \
                    "Size:", ann.getFile().getSize())
                if ann.getFile().getName().endswith('.md.json'):  
                    md_found = True  
                    file_path = os.path.join(destination_path, ann.getFile().getName())
                    with open(str(file_path), 'wb') as f:
                        print("\nDownloading file to", file_path, "...")
                        for chunk in ann.getFileInChunks():
                            f.write(chunk)
                    print("File downloaded!")
        if not md_found:
            raise DataServiceError('Cannot found the metadata file for image:', image.name)            

    def get_processed_data(self, md_uri):
        """Read a processed data from the database

        Parameters
        ----------
        md_uri: str
            URI if the processed data

        Returns
        -------
        ProcessedData object containing the raw data metadata

        """
        # read the data info
        #self._omero_connect()
        try:
            image = self._conn.getObject("Image", md_uri) 
            if image is not None:
                container = ProcessedData()
                container.uuid = image.id
                container.md_uri = image.id
                container.name = image.name
                container.author = image.getDetails().getOwner().getOmeName()
                time_stamp = image.getDetails().getCreationEvent().getTime()
                container.date = datetime.fromtimestamp(time_stamp/1000).strftime('%Y-%m-%d')
                container.format = 'imagetiff' # only image tif 2D gray works now
                container.uri = image.id

                md_json_file = os.path.join(ConfigAccess.instance().config['workspace'], 'processed_data.md.json')    
                self._omero_download_image_md_attachments(image, ConfigAccess.instance().config['workspace'])

                metadata = self._read_json(md_json_file)
                container.run = Container(metadata['origin']['run']["url"], metadata['origin']['run']["uuid"])
                # origin input
                for input_ in metadata['origin']['inputs']:
                    container.inputs.append(
                        ProcessedDataInputContainer(
                            input_['name'],
                            input_['url'],
                            input_['uuid'],
                            input_['type'],
                        )
                    )
                # origin output
                if 'name' in metadata['origin']['output']:
                    container.output['name'] = metadata['origin']['output']["name"]
                if 'label' in metadata['origin']['output']:
                    container.output['label'] = \
                        metadata['origin']['output']['label']

                if os.path.exists(md_json_file):
                    os.remove(md_json_file)
                return container
            else:
                return None   

        finally:
            #self._omero_close()
            pass

    def update_processed_data(self, processed_data):
        """Read a processed data from the database

        Parameters
        ----------
        processed_data: ProcessedData
            Container with the processed data metadata

        """
        #self._omero_connect()
        try:
            image = self._conn.getObject("Image", processed_data.md_uri)     
            image.name = processed_data.name 
            #image.owner = raw_data.author  
            image.date = processed_data.date 
            #image.format = raw_data.format
            image.save()

            # change the attachament file
            # write tmp md.json file
            # origin type
            metadata = dict()
            metadata['origin'] = dict()
            metadata['origin']['type'] = METADATA_TYPE_PROCESSED
            # run url
            metadata['origin']['run'] = {"url": processed_data.run.md_uri,
                                         "uuid": processed_data.run.uuid}
            # origin inputs
            metadata['origin']['inputs'] = list()
            for input_ in processed_data.inputs:
                metadata['origin']['inputs'].append(
                    {
                        'name': input_.name,
                        'url': input_.uri,
                        'uuid': input_.uuid,
                        'type': input_.type,
                    }
                )
            # origin output
            metadata['origin']['output'] = {
                'name': processed_data.output['name'],
                'label': processed_data.output['label'],
            }

            md_json_file = os.path.join(ConfigAccess.instance().config['workspace'], 'processed_data.md.json')
            self._write_json(metadata, md_json_file)

            # set the json file as attachment
            to_delete = []
            for ann in image.listAnnotations():
                if isinstance(ann, omero.gateway.FileAnnotationWrapper) and ann.getFile().getName() == 'processed_data.md.json':
                    to_delete.append(ann.id)
            if len(to_delete) > 0:        
                self._conn.deleteObjects('Annotation', to_delete, wait=True)
            file_ann = self._conn.createFileAnnfromLocalFile(md_json_file, mimetype="text/plain", ns='', desc=None)
            image.linkAnnotation(file_ann)

            if os.path.exists(md_json_file):
                os.remove(md_json_file)

        finally:
            #self._omero_close()
            pass

    def get_dataset(self, md_uri):
        """Read a dataset from the database using it URI

        Parameters
        ----------
        md_uri: str
            URI if the dataset

        Returns
        -------
        Dataset object containing the dataset metadata

        """

        #self._omero_connect()
        container = Dataset()
        try:
            dataset = self._conn.getObject('Dataset', md_uri)
            if dataset is not None:
                container.uuid = dataset.id
                container.md_uri = dataset.id
                container.name = dataset.name
                for image in dataset.listChildren():
                    container.uris.append(Container(image.id, image.id))
            else:
                raise DataServiceError('Dataset not found')
        finally:
            pass
            #self._omero_close()   
        return container      

    def update_dataset(self, dataset):
        """Read a processed data from the database

        Parameters
        ----------
        dataset: Dataset
            Container with the dataset metadata

        """
        #self._omero_connect()
        try:
            dataset = self._conn.getObject('Dataset', dataset.md_uri)
            dataset.name = dataset.name
            dataset.save()
        finally:
            #self._omero_close()
            pass

    def create_dataset(self, experiment, dataset_name):
        """Create a processed dataset in an experiment

        Parameters
        ----------
        experiment: Experiment
            Object containing the experiment metadata
        dataset_name: str
            Name of the dataset

        Returns
        -------
        Dataset object containing the new dataset metadata

        """
        #self._omero_connect()
        try:
            # create dataset
            new_dataset = DatasetWrapper(self._conn, omero.model.DatasetI())
            new_dataset.setName(dataset_name)
            new_dataset.save()
            dataset_obj = new_dataset._obj
            
            # link dataset to project
            link = omero.model.ProjectDatasetLinkI()
            link.setChild(omero.model.DatasetI(dataset_obj.id.val, False))
            link.setParent(omero.model.ProjectI(experiment.md_uri, False))
            self._conn.getUpdateService().saveObject(link)

            container = Dataset()
            container.uuid = new_dataset.id
            container.md_uri = new_dataset.id
            container.name = new_dataset.name
            return container
        finally:
            pass
            #self._omero_close()

    def create_run(self, dataset, run_info):
        """Create a new run metadata

        Parameters
        ----------
        dataset: Dataset
            Object of the dataset metadata
        run_info: Run
            Object containing the metadata of the run. md_uri is ignored and
            created automatically by this method

        Returns
        -------
        Run object with the metadata and the new created md_uri

        """
        #self._omero_connect()
        try:
            omero_dataset = self._conn.getObject('Dataset', dataset.md_uri)

            # create the run annotation file
            ann_files = []
            for ann in omero_dataset.listAnnotations():
                if isinstance(ann, omero.gateway.FileAnnotationWrapper):
                    ann_files.append(ann.getFile().getName())

            run_md_file_name = "run.md.json"
            run_id_count = 0
            while run_md_file_name in ann_files:
                run_id_count += 1
                run_md_file_name = "run_" + str(run_id_count) + ".md.json" 

            file_path = os.path.join(ConfigAccess.instance().config['workspace'], run_md_file_name)
            run_info.processed_dataset = dataset
            run_info.uuid = ''
            run_info.md_uri = file_path
            self._write_run(run_info)

            # upload the annotation file to the dataset
            file_ann = self._conn.createFileAnnfromLocalFile(file_path, mimetype="text/plain", ns='', desc=None)
            omero_dataset.linkAnnotation(file_ann)     # link it to dataset.

            run_info.uuid = file_ann.id
            run_info.md_uri = file_ann.id

            if os.path.exists(file_path):
                os.remove(file_path)
            return run_info

        finally:
            #self._omero_close()
            pass

    def get_dataset_runs(self, dataset):
        """Read the run metadata from a dataset

        Parameters
        ----------
        dataset: Dataset

        Returns
        -------
        List of Runs

        """
        omero_dataset = dataset = self._conn.getObject('Dataset', dataset.md_uri)
        runs = []
        for ann in omero_dataset.listAnnotations():
            if isinstance(ann, omero.gateway.FileAnnotationWrapper):
                if ann.getFile().getName().endswith('md.json'):
                    destination_path = ConfigAccess.instance().config['workspace']       
                    file_path = os.path.join(destination_path, ann.getFile().getName())
                    with open(str(file_path), 'wb') as f:
                        for chunk in ann.getFileInChunks():
                            f.write(chunk)
                    runs.append(self._parse_run(file_path))  
        return runs            

    def _parse_run(self, md_uri):
        # read the file content 
        metadata = self._read_json(md_uri)
        container = Run()
        container.uuid = metadata['uuid']
        container.md_uri = md_uri
        container.process_name = metadata['process']['name']
        container.process_uri =  metadata['process']['url']
        container.processed_dataset = Container(
            metadata['processed_dataset']['url'],
            metadata['processed_dataset']['uuid']
        )
        for input_ in metadata['inputs']:
            container.inputs.append(
                RunInputContainer(
                    input_['name'],
                    input_['dataset'],
                    input_['query'],
                    input_['origin_output_name'],
                )
            )
        for parameter in metadata['parameters']:
            container.parameters.append(
                RunParameterContainer(parameter['name'], parameter['value'])
            )
        return container

    def get_run(self, md_uri):
        """Read a run metadata from the data base

        Parameters
        ----------
        md_uri
            URI of the run entry in the database

        Returns
        -------
        Run: object containing the run metadata

        """
        #self._omero_connect()
        try:
            # copy the file from OMERO
            print('get file of id=', int(md_uri))
            ann = self._conn.getObject('FileAnnotation', int(md_uri))
            print("File ID:", ann.getFile().getId(), ann.getFile().getName(), "Size:", ann.getFile().getSize())

            destination_path = os.path.join(ConfigAccess.instance().config['workspace'], 'run.md.json')        
            with open(str(destination_path), 'wb') as f:
                print("\nDownloading file to", destination_path, "...")
                for chunk in ann.getFileInChunks():
                    f.write(chunk)
            print("File downloaded!")

            # read the file content 
            container = self._parse_run(destination_path)

            if os.path.exists(destination_path):
                os.remove(destination_path)

            return container
        finally:
            pass
            #self._omero_close()

    def _write_run(self, run):
        """Write a run metadata to the data base

        Parameters
        ----------
        run
            Object containing the run metadata

        """
        metadata = dict()
        metadata['uuid'] = run.uuid

        metadata['process'] = {}
        metadata['process']['name'] = run.process_name
        metadata['process']['url'] = run.process_uri
        metadata['processed_dataset'] = {"uuid": run.processed_dataset.uuid,
                                         "url": run.processed_dataset.md_uri}
        metadata['inputs'] = []
        for input_ in run.inputs:
            metadata['inputs'].append(
                {
                    'name': input_.name,
                    'dataset': input_.dataset,
                    'query': input_.query,
                    'origin_output_name': input_.origin_output_name,
                }
            )
        metadata['parameters'] = []
        for parameter in run.parameters:
            metadata['parameters'].append(
                {'name': parameter.name, 'value': parameter.value}
            )

        self._write_json(metadata, run.md_uri)

    def get_data_uri(self, data_container):
        workspace = ConfigAccess.instance().config['workspace']
        extension = FormatsAccess.instance().get(data_container.format).extension
        destination_input = os.path.join(workspace,f"{data_container.name}.{extension}")
        return destination_input

    def create_data_uri(self, dataset, run, processed_data):
        workspace = ConfigAccess.instance().config['workspace']

        extension = FormatsAccess.instance().get(processed_data.format).extension
        processed_data.uri = os.path.join(workspace, f"{processed_data.name}.{extension}")
        return processed_data

    def create_data(self, dataset, run, processed_data):
        """Create a new processed data for a given dataset

        Parameters
        ----------
        dataset: Dataset
            Object of the dataset metadata
        run: Run
            Metadata of the run
        processed_data: ProcessedData
            Object containing the new processed data. md_uri is ignored and
            created automatically by this method

        Returns
        -------
        ProcessedData object with the metadata and the new created md_uri

        """

        # get the parent dataset 
        omero_dataset = self._conn.getObject('Dataset', dataset.md_uri)

        if processed_data.format == 'imagetiff':
            omero_image = self._create_image_data(processed_data.uri, processed_data.name, omero_dataset) 
        else:
            numpy_image = [np.zeros((1,1))]
            def plane_gen():
                for p in numpy_image:
                    yield p
            omero_image = self.conn.createImageFromNumpySeq(plane_gen(), processed_data.name, 
                                                            1, 1, 1, description='',
                                                            dataset=omero_dataset)     
        # set the attachment file
        processed_data.uuid = omero_image.id
        processed_data.md_uri = omero_image.id
        processed_data.uri = omero_image.id
        processed_data.run = run
        self.update_processed_data(processed_data)

        return processed_data    

    def download_data(self, md_uri, destination_file_uri):
        # TODO: Manage other image and file formats    
        if destination_file_uri == '':
            workspace = ConfigAccess.instance().config['workspace'] 
            destination_file_uri = os.path.join(workspace, 'tmp.tif')   

        omero_image = self._conn.getObject("Image", md_uri)    
        channel = 0
        time_point = 0
        image_data = self._download_data(omero_image, channel, time_point)
        imsave(destination_file_uri, image_data)
        return destination_file_uri

    def _download_data(self, img, c=0, t=0):
        """Get one channel and one time point of a data
        
        Parameters
        ----------
        img: omero.gateway.ImageWrapper
            Omero image wrapper
        c: int
            Channel index
        t: int
            Time point index    

        """
        size_z = img.getSizeZ()
        # get all planes we need in a single generator
        zct_list = [(z, c, t) for z in range(size_z)]
        pixels = img.getPrimaryPixels()
        plane_gen = pixels.getPlanes(zct_list)

        if size_z == 1:
            return np.array(next(plane_gen))
        else:
            z_stack = []
            for z in range(size_z):
                # print("plane c:%s, t:%s, z:%s" % (c, t, z))
                z_stack.append(next(plane_gen))
            return np.array(z_stack)

    def _create_image_data(self, source_file_uri, image_name, parent_dataset):

        numpy_image = [imread(source_file_uri)]
        size_z = 1
        size_c = 1
        size_t = 1

        def plane_gen():
            """generator will yield planes"""
            for p in numpy_image:
                yield p

        i = self._conn.createImageFromNumpySeq(plane_gen(), image_name, 
                                              size_z, size_c, size_t, 
                                              description='',
                                              dataset=parent_dataset) 
        return i                                      

    def view_data(self, md_uri):
        raw_data = self.get_raw_data(md_uri)
        if raw_data.format == 'imagetiff':
            return self._omero_image_lazy_loading(md_uri)
        return None  

    def _omero_image_lazy_loading(self, image_id):

        image = self._conn.getObject("Image", image_id)
        nt, nc, nz, ny, nx = [getattr(image, f'getSize{x}')() for x in 'TCZYX']
        pixels = image.getPrimaryPixels()
        dtype = PIXEL_TYPES.get(pixels.getPixelsType().value, None)
        get_plane = delayed(lambda idx: pixels.getPlane(*idx))

        def get_lazy_plane(zct):
            return da.from_delayed(get_plane(zct), shape=(ny, nx), dtype=dtype)

        da_image_list = []
        for c in range(nc):
            t_stacks = []
            for t in range(nt):
                z_stack = []
                for z in range(nz):
                    z_stack.append(get_lazy_plane((z, c, t)))
                t_stacks.append(da.stack(z_stack))
            da_image_list.append(da.stack(t_stacks))
        return da_image_list    
