#!/usr/bin/python3

# Author: D S Pavan Kumar
# Modified by: Tejas Godambe
# Date: April 2017

from subprocess import Popen, PIPE, DEVNULL
import tempfile
import pickle
import struct
import numpy
import os
import time
import keras
import sys

## Data generator class for Kaldi
class dataGenerator_student_tr:
    def __init__ (self, data, ali, exp, batchSize=256):
        self.data = data
        self.ali = ali
        self.exp = exp
        self.batchSize = batchSize

        self.teacher_predictions_file = open (self.data + '/teacher_predictions.ark', 'rb')

        self.maxSplitDataSize = 100
        self.labelDir = tempfile.TemporaryDirectory()
        aliPdf = self.data + '/alipdf.txt'
 
        ## Generate pdf indices
        Popen (['/home/tejas/tools/kaldi/src/bin/ali-to-pdf', exp + '/final.mdl',
                    'ark:gunzip -c %s/ali.*.gz |' % ali,
                    'ark,t:' + aliPdf]).communicate()

        ## Read labels
        with open (aliPdf) as f:
            labels, self.numFeats = self.readLabels (f)
       
        self.inputFeatDim = 429 ## NOTE: HARDCODED. Change if necessary.
        self.outputFeatDim = self.readOutputFeatDim()
        self.splitDataCounter = 0
        
        self.x = numpy.empty ((0, self.inputFeatDim))
        self.y = numpy.empty ((0, self.outputFeatDim)) 

        self.batchPointer = 0
        self.doUpdateSplit = True

        ## Read number of utterances
        with open (data + '/utt2spk') as f:
            self.numUtterances = sum(1 for line in f)
        self.numSplit = - (-self.numUtterances // self.maxSplitDataSize)

        ## Split data dir
        if not os.path.isdir (data + 'split' + str(self.numSplit)):
            Popen (['utils/split_data.sh', '--per-utt', data, str(self.numSplit)]).communicate()
        
        ## Save split labels and delete labels
        self.splitSaveLabels (labels)

    ## Clean-up label directory
    def __exit__ (self):
        self.labelDir.cleanup()
        
    ## Determine the number of output labels
    def readOutputFeatDim (self):
        p1 = Popen (['/home/tejas/tools/kaldi/src/bin/am-info', '%s/final.mdl' % self.exp], stdout=PIPE)
        modelInfo = p1.stdout.read().splitlines()
        for line in modelInfo:
            if b'number of pdfs' in line:
                return int(line.split()[-1])

    ## Read utterance
    def readUtterance (self, ark):
        ## Read utterance ID
        uttId = b''
        c = ark.read(1)
        if not c:
            return None, None
        while c != b' ':
            uttId += c
            c = ark.read(1)
        ## Read feature matrix
        header = struct.unpack('<xcccc', ark.read(5))
        m, rows = struct.unpack('<bi', ark.read(5))
        n, cols = struct.unpack('<bi', ark.read(5))
        featMat = numpy.frombuffer(ark.read(rows * cols * 4), dtype=numpy.float32)
        return uttId.decode(), featMat.reshape((rows,cols))

    def writeUtterance (uttId, featMat, ark, encoding):
        featMat = numpy.asarray (featMat, dtype=numpy.float32)
        m,n = featMat.shape
        ## Write header
        ark.write (struct.pack('<%ds'%(len(uttId)),uttId.encode(encoding)))
        ark.write (struct.pack('<cxcccc',' '.encode(encoding),'B'.encode(encoding),
                    'F'.encode(encoding),'M'.encode(encoding),' '.encode(encoding)))
        ark.write (struct.pack('<bi', 4, m))
        ark.write (struct.pack('<bi', 4, n))
        ## Write feature matrix
        ark.write (featMat)
    
    ## Load labels into memory
    def readLabels (self, aliPdfFile):
        labels = {}
        numFeats = 0
        for line in aliPdfFile:
            line = line.split()
            numFeats += len(line)-1
            labels[line[0]] = [int(i) for i in line[1:]]
        #numBatches = numFeats // self.batchSize 
        return labels, numFeats
    
    ## Save split labels into disk
    def splitSaveLabels (self, labels):
        for sdc in range (1, self.numSplit+1):
            splitLabels = {}
            with open (self.data + '/split' + str(self.numSplit) + '/' + str(sdc) + '/utt2spk') as f:
                for line in f:
                    uid = line.split()[0]
                    if uid in labels:
                        splitLabels[uid] = labels[uid]
            with open (self.labelDir.name + '/' + str(sdc) + '.pickle', 'wb') as f:
                pickle.dump (splitLabels, f)

    ## Convert integer labels to binary
    def getBinaryLabels (self, intLabelList):
        numLabels = len(intLabelList)
        binaryLabels = numpy.zeros ((numLabels, self.outputFeatDim))
        binaryLabels [range(numLabels),intLabelList] = 1
        return binaryLabels
  
    ## Return a minibatch to work on
    def getNextSplitData (self):
        feats = 'scp:' + self.data + '/split' + str(self.numSplit) + '/' + str(self.splitDataCounter) + '/feats.scp'
        p1 = Popen (['/home/tejas/tools/kaldi/src/featbin/splice-feats','--print-args=false','--left-context=5','--right-context=5',
                feats, 'ark:-'], stdout=PIPE)

        with open (self.labelDir.name + '/' + str(self.splitDataCounter) + '.pickle', 'rb') as f:
            labels = pickle.load (f)

        featList = []
        labelList = []
        while True:
            uid, featMat = self.readUtterance (p1.stdout)
            if uid == None:
                if self.numSplit == self.splitDataCounter:
                    self.teacher_predictions_file.close()
                    self.teacher_predictions_file = open (self.data + '/teacher_predictions.ark', 'rb')
                return (numpy.vstack(featList), numpy.vstack(labelList))
            st = time.time()
            uid2, softTargetsMat = self.readUtterance(self.teacher_predictions_file)
            et = time.time()
            #print ('predictions reading time...', et-st)
            assert uid == uid2
            if uid in labels:
                labelMat = self.getBinaryLabels(labels[uid])
                labelMat = labelMat + softTargetsMat
                featList.append (featMat)
                labelList.append (labelMat)


    def __iter__ (self):
        return self

    def __next__ (self):
        while (self.batchPointer + self.batchSize >= len (self.x)):
            if not self.doUpdateSplit:
                self.doUpdateSplit = True
                break

            self.splitDataCounter += 1
            x,y = self.getNextSplitData()
            self.x = numpy.concatenate ((self.x[self.batchPointer:], x))
            self.y = numpy.concatenate ((self.y[self.batchPointer:], y))
            self.batchPointer = 0

            ## Shuffle data
            randomInd = numpy.array(range(len(self.x)))
            numpy.random.shuffle(randomInd)
            self.x = self.x[randomInd]
            self.y = self.y[randomInd]

            if self.splitDataCounter == self.numSplit:
                self.splitDataCounter = 0
                self.doUpdateSplit = False
        
        xMini = self.x[self.batchPointer:self.batchPointer+self.batchSize]
        yMini = self.y[self.batchPointer:self.batchPointer+self.batchSize]
        self.batchPointer += self.batchSize
        return (xMini, yMini)
