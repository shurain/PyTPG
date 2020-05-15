import time
import numpy as np
import gym
import random
from pathlib import Path
import multiprocessing as mp
import matplotlib.pyplot as plt
import pandas as pd

from tpg.trainer import Trainer
from tpg.trainer import loadTrainer
from tpg.agent import Agent

# To transform pixel matrix to a single vector.
def getState(inState):
    # each row is all 1 color
    rgbRows = np.reshape(inState,(len(inState[0])*len(inState), 3)).T

    # add each with appropriate shifting
    # get RRRRRRRR GGGGGGGG BBBBBBBB
    return np.add(np.left_shift(rgbRows[0], 16),
        np.add(np.left_shift(rgbRows[1], 8), rgbRows[2]))

"""
Run each agent in this method for parallization.
Args:
    args: (TpgAgent, envName, scoreList, numEpisodes, numFrames)
"""
def runAgent(args):
    agent = args[0]
    envName = args[1]
    scoreList = args[2]
    numEpisodes = args[3] # number of times to repeat game
    numFrames = args[4] 
    
    # skip if task already done by agent
    if agent.taskDone(envName):
        print('Agent #' + str(agent.agentNum) + ' can skip.')
        scoreList.append((agent.team.id, agent.team.outcomes))
        return
    
    env = gym.make(envName)
    valActs = range(env.action_space.n) # valid actions, some envs are less
    
    scoreTotal = 0 # score accumulates over all episodes
    for ep in range(numEpisodes): # episode loop
        state = env.reset()
        scoreEp = 0
        numRandFrames = 0
        if numEpisodes > 1:
            numRandFrames = random.randint(0,30)
        for i in range(numFrames): # frame loop
            if i < numRandFrames:
                env.step(env.action_space.sample())
                continue

            act = agent.act(frameNumber=i,state=getState(np.array(state, dtype=np.int32)))

            # feedback from env
            state, reward, isDone, debug = env.step(act)
            scoreEp += reward # accumulate reward in score
            if isDone:
                break # end early if losing state
                
        print('Agent #' + str(agent.agentNum) + 
              ' | Ep #' + str(ep) + ' | Score: ' + str(scoreEp))
        scoreTotal += scoreEp
       
    scoreTotal /= numEpisodes
    env.close()
    agent.reward(scoreTotal, envName)
    scoreList.append((agent.team.id, agent.team.outcomes))

def doRun(runInfo):

    #get num actions
    env = gym.make(runInfo['environmentName'])
    numActions = env.action_space.n
    del env

    # Load trainer if one was passed
    if runInfo['loadPath'] is not None:
        trainer = loadTrainer(runInfo['loadPath'])
    else:
        trainer = Trainer(actions=range(numActions), teamPopSize=runInfo['teamPopulationSize'], rTeamPopSize=runInfo['teamPopulationSize'], sharedMemory=runInfo['useMemory'], traversal=runInfo['traversalType'])

    runInfo['trainer'] = trainer #Save the trainer for run details later
    man = mp.Manager()
    pool = mp.Pool(processes=runInfo['numThreads'], maxtasksperchild=1)

    allScores = [] #Track all scores each generation

    for gen in range(runInfo['maxGenerations']): #do maxGenerations of training
        scoreList = man.list()

        # get agents, noRef to not hold reference to trainer in each one
        # don't need reference to trainer in multiprocessing
        agents = trainer.getAgents()

        #run the agents
        pool.map(runAgent,
            [(agent, runInfo['environmentName'], scoreList, runInfo['episodes'], runInfo['numFrames'])
            for agent in agents])

        # apply scores, must do this when multiprocessing
        # because agents can't refer to trainer
        teams = trainer.applyScores(scoreList)


        '''
        Gather statistics 
        '''
        stats = {
            'learnerCount': len(trainer.getAgents(sortTasks=[runInfo['environmentName']])[0].team.learners),
            'instructionCount': 0,
            'add': 0,
            'subtract': 0,
            'multiply': 0,
            'divide': 0,
            'neg':0,
            'memRead':0,
            'memWrite':0,
        }

         #Total instructions in the best root team
        learners = []

        #Collect instruction info!
        trainer.getAgents(sortTasks=[runInfo['environmentName']])[0].team.compileLearnerStats(
            learners,
            stats
            )
     
            
        teams = []
        trainer.getAgents(sortTasks=[runInfo['environmentName']])[0].team.size(teams)
        print("root team size: " + str(len(teams)))

        #Save best root team each generation
        Path(runInfo['resultsPath']+"teams/").mkdir(parents=True, exist_ok=True)
        trainer.getAgents(sortTasks=[runInfo['environmentName']])[0].saveToFile(runInfo['resultsPath'] + "teams/root_team_gen_" + str(gen))

        #Save the trainer as a 'checkpoint' should we want to restart the run from this generation
        Path(runInfo['resultsPath']+"trainers/").mkdir(parents=True, exist_ok=True)
        trainer.saveToFile(runInfo['resultsPath']+"trainers/gen_" + str(gen))

        # important to remember to set tasks right, unless not using task names
        # task name set in runAgent()
        trainer.evolve(tasks=[runInfo['environmentName']]) # go into next gen
        
        # an easier way to track stats than the above example
        scoreStats = trainer.fitnessStats
        allScores.append((scoreStats['min'], scoreStats['max'], scoreStats['average']))
        
       
        print('Time Taken (Hours): ' + str((time.time() - runInfo['tStart'])/3600))
        print('Gen: ' + str(gen))
        print('Results so far: ' + str(allScores))

        runStatsFile = open(runInfo['resultsPath'] + runInfo['runStatsFileName'],"a")
        
        runStatsFile.write(str(gen) + "," +
        str((time.time() - runInfo['tStart'])/3600) + "," + 
        str(scoreStats['min']) + "," +
        str(scoreStats['max']) + "," +
        str(scoreStats['average']) + "," +
        str(len(learners)) + "," +
        str(len(teams)) + "," + 
        str(stats['instructionCount']) + "," +
        str(stats['add']) + "," +
        str(stats['subtract']) + "," +
        str(stats['multiply']) + "," +
        str(stats['divide']) + "," + 
        str(stats['neg']) + "," +
        str(stats['memRead']) + "," +
        str(stats['memWrite']) + "\n"
        )
        runStatsFile.flush()
        runStatsFile.close()

    #Return scores and trainer for additional metrics post-run
    return allScores, trainer

def writeRunInfo(runInfo):

    file = open(runInfo['resultsPath']+runInfo['runInfoFileName'], 'w')
    file.write("host = " + runInfo['hostname']+ "\n")
    file.write("startTime = " + runInfo['startTime']+ "\n")
    file.write("tStart = " + str(runInfo['tStart'])+ "\n")
    file.write("environmentName = " + runInfo['environmentName']+ "\n")
    file.write("maxGenerations = " + str(runInfo['maxGenerations'])+ "\n")
    file.write("episodes = " + str(runInfo['episodes'])+ "\n")
    file.write("numFrames = " + str(runInfo['numFrames'])+ "\n")
    file.write("threads = " + str(runInfo['numThreads'])+ "\n")
    file.write("teamPopulationSize = " + str(runInfo['teamPopulationSize'])+ "\n")
    file.write("useMemory = " + str(runInfo['useMemory'])+ "\n")
    file.write("traversalType = " + str(runInfo['traversalType'])+ "\n")
    file.write("resultsPath = " + str(runInfo['resultsPath'])+ "\n")
    file.write("msGraphConfigPath = " + str(runInfo['msGraphConfigPath'])+ "\n")
    file.write("emailListPath = " + runInfo['emailListPath']+ "\n")
    file.write("emailList: \n")
    for email in runInfo['emailList']:
        file.write("\t" + email+ "\n")
    file.write("loadPath = " + str(runInfo['loadPath'])+ "\n")
    file.close()

def generateGraphs(runInfo):
    runData = pd.read_csv(
        runInfo['resultsPath'] + runInfo['runStatsFileName'],
        sep=',',
        header=0,
        dtype={
            'generation':'np.uint32',
            'time taken':'np.float64',
            'min fitness':'np.float32',
            'avg fitness':'np.float32',
            'max fitness':'np.float32',
            'num learners':'np.uint32',
            'num teams in root team':'np.uint32',
            'num instructions':'np.uint32',
            'add':'np.uint32',
            'sub':'np.uint32',
            'mult':'np.uint32',
            'div':'np.uint32',
            'neg':'np.uint32',
            'memRead':'np.uint32',
            'memWrite':'np.uint32'
        }
        ).to_numpy()

    print(runData.dtype.names)
    print(runData.shape)
    print(runData[:,:])

    #Max Fitness Graph
    plt.figure()
    x = runData[:,0]
    y = runData[:,3]
    plt.plot(
        x, #x
        y #y
        )
    plt.xlabel("Generation #")
    print('shape of x ' + str(x.shape))
    print('shape of y ' + str(y.shape))
    plt.xticks( np.linspace(min(x), max(x), 20))
    plt.ylabel("Max Fitness")
    plt.yticks ( np.linspace(min(y),max(y),20))
    plt.title("Max Fitness")

    plt.savefig(runInfo['resultsPath']+runInfo['maxFitnessFile'], format='svg')

    #Avg Fitness Graph
    plt.figure()
    x = runData[:,0]
    y = runData[:,4]
    plt.scatter(
        x=x,
        y=y
    )
    plt.xlabel("Generation #")
    plt.xticks( np.linspace(min(x), max(x), 20))
    plt.ylabel("Avg Fitness")
    plt.yticks ( np.linspace(min(y),max(y),20))
    plt.title("Avg Fitness")

    plt.savefig(runInfo['resultsPath']+runInfo['avgFitnessFile'], format='svg')

    #Min Fitness Graph
    plt.figure()
    x = runData[:,0]
    y = runData[:,2]
    plt.plot(
        x,
        y
    )
    plt.xlabel("Generation #")
    plt.xticks( np.linspace(min(x), max(x), 20))
    plt.ylabel("Min Fitness")
    plt.yticks ( np.linspace(min(y),max(y),20))
    plt.title("Min Fitness")

    plt.savefig(runInfo['resultsPath']+runInfo['minFitnessFile'], format='svg')

    #Time Taken Graph
    plt.figure()
    x = runData[:,0]
    y = runData[:,1]
    plt.scatter(
        x=x,
        y=y   
    )
    plt.xlabel("Generation #")
    plt.xticks( np.linspace(min(x), max(x), 20))
    plt.ylabel("Time Taken (hours)")
    plt.yticks ( np.linspace(min(y),max(y),20))
    plt.title("Time Taken")

    plt.savefig(runInfo['resultsPath']+runInfo['timeTakenFile'], format='svg')

    #Instructions Composition Graph
    plt.figure()

    generations = runData[:,0]

    adds = runData[:,8]
    subs = runData[:,9]
    mults = runData[:,10]
    divs = runData[:,11]
    negs = runData[:,12]
    memReads = runData[:,13]
    memWrites = runData[:,14]
    ind = [x for x, _ in enumerate(generations)]

    plt.bar(ind, memWrites, label="memWrites", bottom=memReads+negs+divs+mults+subs+adds)
    plt.bar(ind, memReads, label="memReads", bottom=negs+divs+mults+subs+adds)
    plt.bar(ind, negs, label="negs",bottom=divs+mults+subs+adds)
    plt.bar(ind, divs, label="div",bottom=mults+subs+adds)
    plt.bar(ind, mults, label="mult", bottom=subs+adds)
    plt.bar(ind, subs, label="sub",bottom=adds)
    plt.bar(ind, adds, label="add")

    plt.xticks(ind, generations)
    plt.ylabel("# of Instructions")
    plt.xlabel("Generation #")
    plt.legend(loc="upper right")
    plt.title("Instruction Composition")

    plt.savefig(runInfo['resultsPath']+runInfo['instructionCompositionFile'], format='svg')

    #Learners in Root Team
    plt.figure()
    generations = runData[:,0]
    learners = runData[:,5]
    ind = [x for x, _ in enumerate(generations)]

    plt.bar(ind, learners)
    plt.xlabel("Generation #")
    plt.ylabel("# of Learners in Root Team")
    plt.title("Learners in Root Teams")
    plt.xticks(ind, generations)

    plt.savefig(runInfo['resultsPath']+runInfo['learnersFile'], format='svg')

    #Teams in Root Team
    plt.figure()
    generations = runData[:,0]
    teams = runData[:,6]
    ind = [x for x, _ in enumerate(generations)]

    plt.bar(ind, teams)
    plt.xlabel("Generation #")
    plt.ylabel("# of Teams in Root Team")
    plt.title("Teams in Root Teams")

    plt.savefig(runInfo['resultsPath']+runInfo['teamsFile'], format='svg')

    #Total Instructions Graph
    plt.figure()

    generations = runData[:,0]
    totalInstructions = runData[:,7]
    ind = [indx for indx, _ in enumerate(generations)]

    plt.bar(ind, totalInstructions)
    plt.xlabel('Generation #')
    plt.ylabel('# of Instructions')
    plt.title("Total Instructions")
    plt.xticks(ind,generations)

    plt.savefig(runInfo['resultsPath']+runInfo['instructionsFile'], format='svg')

    #Load Root Team Fitness Data
    rtfData = pd.read_csv(runInfo['resultsPath']+runInfo['finalRootTeamFitnessFileName'], sep=',',header=0).to_numpy()

    print(rtfData.dtype.names)
    print(rtfData.shape)
    print(rtfData)

    rtfData = rtfData[rtfData[:,1].argsort()[::-1]]

    print(rtfData)

    #Root Team Fitness Graph
    plt.figure()

    teamIds = rtfData[:,0]
    fitnesses = rtfData[:,1]
    ind = [x for x, _ in enumerate(teamIds)]

    plt.bar(ind, fitnesses)
    plt.xlabel("Team Ids")
    plt.ylabel("Fitness")
    plt.title("Final Root Teams Fitness")
    plt.xticks(ind, teamIds)

    plt.savefig(runInfo['resultsPath']+runInfo['rootTeamsFitnessFile'], format='svg')

#If the results path already exists, add an underscore + number to it until it doesn't exist
def determineResultsPath(resultsPath):

    if Path(resultsPath).exists():
        counter = 1
        token = resultsPath[:len(resultsPath)-1]
        while Path(token + '_' + str(counter)).exists():
            counter += 1
        return token + '/'
    else:
        return resultsPath
    


