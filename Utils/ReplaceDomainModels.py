import os
import sys
#import subprocess

import numpy as np
import getopt
import shutil

from Common import PDBUtils
from Common.SequenceUtils import LoadFASTAFile

## this script replaces one or multiple domain models in a whole-chain model through model alignment
## please make sure DeepScore is in bin path
def Usage():
	print 'python ReplaceDomainModels.py [-d savefolder | -r ] WholeChainModelFile D1ModelFile D2ModelFile ...'
	print '	This script replace one or multiple domain models in a whole-chain model through model structure alignment'
	print '		Ideally, one model to be replaced shall be similar to its replacement'
	print '		Please make sure that an exectuable program DeepScore is available'
	print '	WholeChainModelFile: an whole-chain model to be used as the overall scaffold. In this file residue number shall start from 1 and there shall be no missing residues, otherwise the result may be incorrect'
	print '	DModelFiles: the PDB files for replacement models. Right now one domain can only correspond to one subsequence of the whole-chain sequence'
	print '	The resultant file will be named after bname-dName1-dName2....pdb where bname is the base name of WholeChainModelFile and dName is the base name of domain model files'
	print '	-r: if specified, residues in the whole-chain model but not covered by domain models will be discarded. By default, this kind of residues will be kept in the resultant model file'
	print '	-d: the folder for result saving, default current work directory'

## here we assume that the local quality (distance deviation) occpuies columns 61-66 in the ATOM record of a PDB file
def ExtractLocalQuality(pdbfile):
	with open(pdbfile, 'r') as fh:
		CAquality = [ line.strip()[60:66] for line in list(fh) if line.startswith('ATOM ') and line[12:16] == ' CA ' ]

	#print CAquality
	for qStr in CAquality:
		q = np.float32(qStr[:6])
		if q<0 or q>100:
			print 'WARNING: some local quality is not in [0, 100]. Please double check to make sure this pdb file indeed contains local error estimate: ', pdbfile
			break

	return CAquality

## extract new domain model from a structure alignment file
## here modelFile has the superimposition of the two models generated by DeepScore
## we read from top line until seeing 'TER'
def ExtractNewDomainModel(modelFile):
	with open(modelFile, 'r') as fh:
		content =  [line.strip() for line in list(fh)]

	atomRecords = []
	for c in content:
		if c.startswith('TER'):
			break;
		if not c.startswith('ATOM'):
			continue
		atomRecords.append(c)
	return atomRecords

## filter is a list of flags, each for one residue in modelFile
## When one filter entry is set to True, ignore its corresponding residue
def ExtractAtomRecords(modelFile, filter=None):
	with open(modelFile, 'r') as fh:
		content =  [line.strip() for line in list(fh)]

	atomRecords = []
	for c in content:
		if not c.startswith('ATOM'):
			continue

		resNumber = np.int32(c[22:26])
		if filter is not None and filter[resNumber-1]:
			continue

		atomNumber = np.int32(c[6:11])
		atomRecords.append( (atomNumber, resNumber, c) )

	return atomRecords


if __name__ == "__main__":

	if len(sys.argv) < 3:
		Usage()
		exit(1)
	
	try:
		opts, args = getopt.getopt(sys.argv[1:], "d:r", ["savefolder==", "replaceAllResidues=="])
	except getopt.GetoptError as err:
		Usage()
		exit(1)

	savefolder = os.getcwd()
	replaceAllResidues = False

	for opt, arg in opts:
		if opt in ("-d", "--savefolder"):
			savefolder = arg
			if not os.path.isdir(savefolder):
				os.mkdir(savefolder)
		elif opt in ("-r", "--replaceAllResidues"):
			replaceAllResidues = True

		else:
			Usage()
			exit(1)

	if len(args) < 2:
		Usage()
		exit(1)

	initModelFile = args[0]
	if not os.path.isfile(initModelFile):
		print "ERROR: invalid whole-chain model file: ", initModelFile
		exit(1)

	pdbseqs, _, chains = PDBUtils.ExtractSeqFromPDBFile(initModelFile)
	assert len(pdbseqs) == 1
	mainSeq = pdbseqs[0]
	quality = ExtractLocalQuality(initModelFile)

	domainModels = args[1:]
	domainSeqs = []
	domainQualitys = []
	for dmodel in domainModels:
		pdbseqs, _, chains = PDBUtils.ExtractSeqFromPDBFile(dmodel)
		assert len(pdbseqs) == 1
		domainSeqs.append( pdbseqs[0] )
		locQuality = ExtractLocalQuality(dmodel)
		assert len(locQuality) == len(pdbseqs[0])
		domainQualitys.append( locQuality )

	## record if one residue is replaced or not
	replacedFlags = [ False ] * len(mainSeq)

	## align domainSeqs to mainSeq by sequence alignment
	startPositions = []
	for dSeq, dQuality in zip(domainSeqs, domainQualitys):
		index = mainSeq.find(dSeq)
		if index < 0:
			print 'ERROR: cannot map the domain sequence to the whole-chain sequence'
			print 'domain seq: ', dSeq
			print 'whole  seq: ', mainSeq
			exit(1)
		quality[index: index + len(dSeq)] = dQuality
		replacedFlags[index: index + len(dSeq)] = [True] * len(dSeq)
		startPositions.append(index)


	## align domain models to the whole-chain model by DeepScore
	pid = os.getpid()
	newDomainModelRecords = []
	for dmodel in domainModels:
		newDomainModel = 'tmpRX' + str(pid) + os.path.basename(dmodel)
		cmds = ['DeepScore', dmodel, initModelFile, '-o', newDomainModel]
		cmdStr = ' '.join(cmds)
		os.system(cmdStr)

		## extract corresponding domain model coordinates
		atomRecords = ExtractNewDomainModel(newDomainModel + '.pdb')
		newDomainModelRecords.append(atomRecords)

		## remove temporary files
		cmds = ['rm', '-f', newDomainModel + '.*']
		os.system( ' '.join(cmds) )

	## change residue number and atom number
	newAtomRecords = []
	for startPos, dAtomRecords in zip(startPositions, newDomainModelRecords):
		resNumber = startPos
		## here atomNumber is only a temporary number and will be revised later
		atomNumber = resNumber * 50 
		prevResNumber = -10000
		for atomRecord in dAtomRecords:
			##atomRecord[22:26] is the residue number
			currResNumber = np.int32(atomRecord[22:26])
			if currResNumber != prevResNumber:
				resNumber += 1
				prevResNumber = currResNumber

			newAtomRecords.append( (atomNumber, resNumber, atomRecord) )
			atomNumber += 1

	## sort atomRecords by resNumber and atomNumber
	if not replaceAllResidues:
		orgAtomRecords = ExtractAtomRecords(initModelFile, replacedFlags)
	else:
		orgAtomRecords = []

	## sort all atom records by (resNumber, atomNumber)
	finalAtomRecords = sorted(newAtomRecords + orgAtomRecords, key=lambda x:(x[1], x[0]) )

	## write the final atom records including local quality
	outStrs = []
	for atomNumber, record in zip(range(1, 1+len(finalAtomRecords)), finalAtomRecords):
		atomRecord = record[2]
		resNumber = record[1]

		##'ATOM '
		firstPart = atomRecord[0:6]

		## atom number
		secondPart = '{:5d}'.format(atomNumber)

		## atom name, atlLoc, residue name, chain ID
		thirdPart = atomRecord[11:22]

		## the residue number
		forthPart = '{:4d}'.format(record[1])

		## insertion code, coordinates, occupany
		fifthPart = atomRecord[26:60]

		## error estimate
		sixthPart = quality[resNumber - 1]

		newRecord = firstPart + secondPart + thirdPart + forthPart + fifthPart + sixthPart
		outStrs.append(newRecord)
	
	## write to a file
	names = [ os.path.basename(initModelFile).split('.')[0] ] + [ os.path.basename(dModelFile).split('.')[0] for dModelFile in domainModels ]
	mainName = '-'.join(names)
	savefile = mainName + '.pdb'
	savefile = os.path.join(savefolder, savefile)

	with open(savefile, 'w') as fh:
		fh.writelines('\n'.join(outStrs) )			
