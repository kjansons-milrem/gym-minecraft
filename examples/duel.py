import argparse
import csv
import time
import gym
import gym_minecraft
from gym.spaces import Box, Discrete
from keras.models import Model
from keras.layers import Input, Permute, Convolution2D, Flatten, Dense, Lambda
from keras.optimizers import Adam, RMSprop
from keras import backend as K
import numpy as np
from buffer import Buffer

parser = argparse.ArgumentParser()
parser.add_argument('--batch_size', type=int, default=32)
parser.add_argument('--hidden_size', type=int, default=100)
parser.add_argument('--layers', type=int, default=1)
parser.add_argument('--train_repeat', type=int, default=4)
parser.add_argument('--gamma', type=float, default=0.99)
parser.add_argument('--tau', type=float, default=0.001)
parser.add_argument('--episodes', type=int, default=1000)
parser.add_argument('--replay_size', type=int, default=500000)
parser.add_argument('--max_timesteps', type=int, default=1000)
parser.add_argument('--activation', choices=['tanh', 'relu'], default='relu')
parser.add_argument('--optimizer', choices=['adam', 'rmsprop'], default='adam')
parser.add_argument('--optimizer_lr', type=float)
parser.add_argument('--exploration', type=float, default=0.1)
parser.add_argument('--advantage', choices=['naive', 'max', 'avg'], default='naive')
parser.add_argument('--display', action='store_true', default=True)
parser.add_argument('--no_display', dest='display', action='store_false')
parser.add_argument('--gym_record')
parser.add_argument('--save_csv')
parser.add_argument('environment')
args = parser.parse_args()

env = gym.make(args.environment)
env.unwrapped.init(videoResolution=[40, 30], allowDiscreteMovement=["move", "turn"], log_level='INFO')
assert isinstance(env.observation_space, Box)
assert isinstance(env.action_space, Discrete)

if args.gym_record:
    env.monitor.start(args.gym_record)

if args.save_csv:
    csv_file = open(args.save_csv, "wb")
    csv_writer = csv.writer(csv_file)
    csv_writer.writerow((
          "episode",
          "episode_reward",
          "average_reward",
          "min_reward",
          "max_reward",
          "last_exploration_rate",
          "total_train_steps",
          "replay_memory_count",
          "meanq",
          "meancost",
          "episode_time",
          "episode_steps",
          "steps_per_second"
        ))
    csv_file.flush()


def createLayers():
    x = Input(shape=env.observation_space.shape)
    h = Lambda(lambda a: a / 255.0)(x)
    h = Permute((3, 1, 2))(h)
    h = Convolution2D(32, 4, 4, subsample=(2, 2), activation=args.activation)(h)
    h = Convolution2D(64, 4, 4, subsample=(2, 2), activation=args.activation)(h)
    h = Flatten()(h)
    for i in xrange(args.layers):
        h = Dense(args.hidden_size, activation=args.activation)(h)
    y = Dense(env.action_space.n + 1)(h)
    if args.advantage == 'avg':
        z = Lambda(lambda a: K.expand_dims(a[:, 0], dim=-1) + a[:, 1:] - K.mean(a[:, 1:], keepdims=True), output_shape=(env.action_space.n,))(y)
    elif args.advantage == 'max':
        z = Lambda(lambda a: K.expand_dims(a[:, 0], dim=-1) + a[:, 1:] - K.max(a[:, 1:], keepdims=True), output_shape=(env.action_space.n,))(y)
    elif args.advantage == 'naive':
        z = Lambda(lambda a: K.expand_dims(a[:, 0], dim=-1) + a[:, 1:], output_shape=(env.action_space.n,))(y)
    else:
        assert False

    return x, z

if args.optimizer == 'adam':
    if args.optimizer_lr:
        optimizer = Adam(args.optimizer_lr)
    else:
        optimizer = args.optimizer
elif args.optimizer == 'rmsprop':
    if args.optimizer_lr:
        optimizer = RMSprop(args.optimizer_lr)
    else:
        optimizer = args.optimizer
else:
    assert False, "Unknown optimizer " + args.optimizer

x, z = createLayers()
model = Model(input=x, output=z)
model.summary()
model.compile(optimizer=args.optimizer, loss='mse')

x, z = createLayers()
target_model = Model(input=x, output=z)
target_model.set_weights(model.get_weights())

mem = Buffer(args.replay_size, env.observation_space.shape, (1,))

all_rewards = []
total_train_steps = 0
for i_episode in xrange(args.episodes):
    observation = env.reset()
    episode_reward = 0
    maxqs = []
    costs = []
    begin = time.time()
    steps = 0
    for t in xrange(args.max_timesteps):
        if args.display:
            env.render()

        if np.random.random() < args.exploration:
            action = env.action_space.sample()
        else:
            s = np.array([observation])
            q = model.predict_on_batch(s)
            #print "q:", q
            action = np.argmax(q[0])
            maxqs.append(np.max(q[0]))
        #print "action:", action

        prev_observation = observation
        observation, reward, done, info = env.step(action)
        #print info
        episode_reward += reward
        #print "reward:", reward
        mem.add(prev_observation, np.array([action]), reward, observation, done)

        for k in xrange(args.train_repeat):
            prestates, actions, rewards, poststates, terminals = mem.sample(args.batch_size)

            qpre = model.predict_on_batch(prestates)
            qpost = target_model.predict_on_batch(poststates)
            for i in xrange(qpre.shape[0]):
                if terminals[i]:
                    qpre[i, actions[i]] = rewards[i]
                else:
                    qpre[i, actions[i]] = rewards[i] + args.gamma * np.amax(qpost[i])
            cost = model.train_on_batch(prestates, qpre)
            costs.append(cost)
            total_train_steps += 1

            weights = model.get_weights()
            target_weights = target_model.get_weights()
            for i in xrange(len(weights)):
                target_weights[i] = args.tau * weights[i] + (1 - args.tau) * target_weights[i]
            target_model.set_weights(target_weights)

        steps += 1
        if done:
            break

    print "Episode {} finished after {} timesteps, episode reward {}".format(i_episode + 1, t + 1, episode_reward)
    all_rewards.append(episode_reward)

    elapsed = time.time() - begin
    if args.save_csv:
        csv_writer.writerow((
              i_episode + 1,
              episode_reward,
              np.mean(all_rewards),
              np.min(all_rewards),
              np.max(all_rewards),
              args.exploration,
              total_train_steps,
              mem.count,
              np.mean(maxqs),
              np.mean(costs),
              elapsed,
              steps,
              steps / elapsed
            ))
        csv_file.flush()

print "Average reward per episode {}".format(np.mean(all_rewards))

if args.gym_record:
    env.monitor.close()
