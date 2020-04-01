import math
import numpy as np
import pickle
import time
import datetime

import gym
from gym import spaces
from pybullet_fingers.sim_finger import SimFinger
from pybullet_fingers.gym_wrapper.data_logger import DataLogger
from pybullet_fingers.gym_wrapper.finger_spaces import FingerSpaces
from pybullet_fingers.gym_wrapper import utils


class FingerReach(gym.Env):
    """
    A gym environment to enable training on either the single or
    the tri-fingers robots for the task of reaching

    Args:
        control_rate_s (float): the rate at which the env step runs
        enable_visualization (bool): if the simulation env is to be
            visualized
        finger-type (str "single"/"tri"): to train on the "single"
            or the "tri" finger
        smoothing_params:
            num_episodes: the total number of episodes for which the training
                is performed
            start_after: the fraction of episodes after which the smoothing of
                applied actions to the motors should start
            final_alpha: smoothing coeff that will be reached at the end of the
                smoothing
            stop_after: the fraction of total episodes by which final alpha is
                to be reached, after which the same final alpha will be used
                for smoothing in the remainder of the episodes
        velocity_cost_factor (float): The factor by which a velocity
            related component to the total reward is to be added
            ([default] 0)
        sampling_strategy (str [default]"separated"/"triangle"): the
            strategy according to which the goals are sampled
            ([default] "separated")
        use_real_robot (bool): if the model was trained on the
            real robot
            ([default] False)
        finger_config_suffix (arg use-real): which finger was trained
            ([default] 0)
    """

    def __init__(self,
                 control_rate_s,
                 enable_visualization,
                 finger_type,
                 smoothing_params,
                 velocity_cost_factor=0,
                 sampling_strategy="separated",
                 use_real_robot=False,
                 finger_config_suffix="0",
                 synchronize=False,
                 ):
        """
        Constructor sets up smoothing, the finger robot depending on whether
        the simulated or the real one is to be used, sets up the physical world
        parameters, and resets to begin training.
        """

        self.logger = DataLogger()

        if finger_type == "single":
            self.num_fingers = 1
        else:
            self.num_fingers = 3

        simulation_rate_s = 0.004
        self.steps_per_control = int(round(control_rate_s / simulation_rate_s))
        assert(abs(control_rate_s - self.steps_per_control * simulation_rate_s)
               <= 0.000001)

        if "is_test" in smoothing_params:
            self.smoothing_start_episode = 0
            self.smoothing_alpha = smoothing_params["final_alpha"]
            self.smoothing_increase_step = 0
            self.smoothing_stop_episode = math.inf
        else:
            self.smoothing_stop_episode = int(smoothing_params["num_episodes"]
                                              *
                                              smoothing_params["stop_after"])

            self.smoothing_start_episode = int(smoothing_params["num_episodes"]
                                               *
                                               smoothing_params["start_after"])
            num_smoothing_increase_steps = (self.smoothing_stop_episode -
                                            self.smoothing_start_episode)
            self.smoothing_alpha = 0
            self.smoothing_increase_step = (smoothing_params["final_alpha"] /
                                            num_smoothing_increase_steps)

        self.smoothed_action = None
        self.episode_count = 0

        self.observations_keys = [
            'joint_positions',
            'joint_velocities',
            'goal_position',
            'action_joint_positions'
        ]

        self.observations_sizes = [
            3 * self.num_fingers,
            3 * self.num_fingers,
            3 * self.num_fingers,
            3 * self.num_fingers
        ]

        self.spaces = FingerSpaces(num_fingers=self.num_fingers,
                                   observations_keys=self.observations_keys,
                                   observations_sizes=self.observations_sizes,
                                   separate_goals=True)

        if use_real_robot:
            from pybullet_fingers.real_finger import RealFinger
            self.finger = RealFinger(
                enable_visualization=enable_visualization,
                finger_type=finger_type,
                action_bounds=self.spaces.action_bounds,
                finger_config_suffix=finger_config_suffix,
                sampling_strategy=sampling_strategy)

        else:
            self.finger = SimFinger(time_step=simulation_rate_s,
                                    enable_visualization=enable_visualization,
                                    finger_type=finger_type,
                                    action_bounds=self.spaces.action_bounds,
                                    sampling_strategy=sampling_strategy)

        gym.Env.__init__(self)
        self.metadata = {'render.modes': ['human']}

        self.velocity_cost_factor = velocity_cost_factor

        self.unscaled_observation_space = \
            self.spaces.get_unscaled_observation_space()
        self.unscaled_action_space = self.spaces.get_unscaled_action_space()

        self.observation_space = self.spaces.get_scaled_observation_space()
        self.action_space = self.spaces.get_scaled_action_space()

        self.finger.display_goal()

        self.seed()

        if synchronize:
            now = datetime.datetime.now()
            self.next_start_time = datetime.datetime(
                now.year, now.month, now.day, now.hour, now.minute + 1)
        else:
            self.next_start_time = None

        self.reset()

    def _compute_reward(self, observation, goal):
        """
        The reward function of the environment

        Args:
            observation (list): the observation at the
                current step
            goal (list): the desired goal for the episode

        Returns:
            the reward, and the done signal
        """
        joint_positions = observation[
            self.spaces.key_to_index['joint_positions']]

        end_effector_positions = self.finger.forward_kinematics(
            np.array(joint_positions))

        velocity = np.linalg.norm(
            observation[self.spaces.key_to_index['joint_velocities']])

        # TODO is matrix norm really always same as vector norm on flattend
        # matrices?
        distance_to_goal = utils.compute_distance(end_effector_positions, goal)

        reward = -distance_to_goal - self.velocity_cost_factor * velocity
        done = False

        return reward * self.steps_per_control, done

    def _get_observation(self, action, log_observation=False):
        """
        Get the current observation from the env for the agent

        Args:
            log_observation (bool): specify whether you want to
                log the observation

        Returns:
            observation (list): comprising of the observations corresponding
                to the key values in the observation_keys
        """
        tip_positions = self.finger.forward_kinematics(self.
                                                       finger.
                                                       observation.position)
        end_effector_position = np.concatenate(tip_positions)
        joint_positions = self.finger.observation.position
        joint_velocities = self.finger.observation.velocity
        flat_goals = np.concatenate(self.goal)
        end_effector_to_goal = list(np.subtract(flat_goals,
                                                end_effector_position))

        observation_dict = {}
        observation_dict['end_effector_position'] = end_effector_position
        observation_dict['joint_positions'] = joint_positions
        observation_dict['joint_velocities'] = joint_velocities
        observation_dict['end_effector_to_goal'] = end_effector_to_goal
        observation_dict['goal_position'] = flat_goals
        observation_dict['action_joint_positions'] = action

        if log_observation:
            self.logger.append(joint_positions, end_effector_position,
                               time.time())
        observation = [v
                       for key in self.observations_keys
                       for v in observation_dict[key]]

        return observation

    def step(self, action):
        """
        The env step method

        Args:
            action (list): the joint positions that have to be achieved

        Returns:
            the observation scaled to lie between [-1;1], the reward,
            the done signal, and info on if the agent was successful at
            the current step
        """
        # Unscale the action to the ranges of the action space of the
        # environment, explicitly (as the prediction from the network
        # lies in the range [-1;1])
        unscaled_action = utils.unscale(action, self.unscaled_action_space)

        # smooth the action by taking a weighted average with the previous
        # action, where the weight, ie, the smoothing_alpha is gradually
        # increased at every episode reset (see the reset method for details)
        if self.smoothed_action is None:
            # start with current position
            # self.smoothed_action = self.finger.observation.position
            self.smoothed_action = unscaled_action

        self.smoothed_action = (self.smoothing_alpha * self.smoothed_action +
                                (1 - self.smoothing_alpha) * unscaled_action)

        # this is the control loop to send the actions for a few timesteps
        # which depends on the actual control rate
        observation = None
        for _ in range(self.steps_per_control):
            self.finger.set_action(self.smoothed_action, "position")
            self.finger.step_robot(observation is None)
            # get observation from first iteration (when action is applied the
            # first time)
            if observation is None:
                observation = self._get_observation(self.smoothed_action, True)
        reward, done = self._compute_reward(observation, self.goal)
        info = {'is_success': np.float32(done)}
        scaled_observation = utils.scale(observation,
                                         self.unscaled_observation_space)
        return scaled_observation, reward, done, info

    def reset(self):
        """
        Episode reset

        Returns:
            the scaled to [-1;1] observation from the env after the reset
        """
        # synchronize episode starts with wall time
        # (freeze the robot at the current position before starting the sleep)
        if self.next_start_time:
            try:
                self.finger.set_action(self.finger.observation.position,
                                       "position")
                self.finger.step_robot(True)
            except Exception:
                pass

            utils.sleep_until(self.next_start_time)
            self.next_start_time += datetime.timedelta(seconds=4)

        self.update_smoothing()
        self.episode_count += 1
        self.smoothed_action = None

        action = self.finger.reset_finger()

        target_joint_config = np.asarray(
            self.finger.sample_random_joint_positions_for_reaching())
        self.goal = self.finger.forward_kinematics(target_joint_config)

        self.logger.new_episode(target_joint_config, self.goal)

        self.finger.reset_goal_markers(self.goal)

        return utils.scale(self._get_observation(action=action),
                           self.unscaled_observation_space)

    def render(self, mode='human'):
        """
        Connect to the simulation in the GUI mode
        """
        self.finger.connect_to_simulation(enable_visualization=True)

    def update_smoothing(self):
        """
        Update the smoothing coefficient with which the action to be
        applied is smoothed
        """
        if self.smoothing_start_episode <= \
                self.episode_count < self.smoothing_stop_episode:
            self.smoothing_alpha += self.smoothing_increase_step
        print("episode: {}, smoothing: {}".format(self.episode_count,
                                                  self.smoothing_alpha))