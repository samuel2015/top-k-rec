"""
    Matrix Factorization (MF) based on Content-aware Bayesian Personalized Ranking (VBPR)
    Sampling Method : uniform item sampling per user
    Author          : Xingzhong Du
    E-mail          : dxz.nju@gmail.com
    Reference       : "VBPR: Visual Bayesian Personalized Ranking from Implicit Feedback", Ruining He, Julian McAuley
"""

from .bpr import BPR
from collections import defaultdict
from utils import get_id_dict_from_file 
import numpy as np
import os
import pickle
import scipy.sparse as ss
import sys
import tensorflow as tf
import time

class VBPR(BPR):
    def __init__(self, k, d, lambda_u=2.5e-3, lambda_i=2.5e-3, lambda_j=2.5e-4, lambda_b=0, lambda_e=0, lr=1.0e-4, mode='l2'):
        BPR.__init__(self, k, lambda_u, lambda_i, lambda_j, lambda_b, lr, mode);
        self.d = d;
        self.le = lambda_e;

    def load_content_data(self, content_file, iid_file):
        print ('Load content data from %s'%(content_file));
        iids = get_id_dict_from_file(iid_file);
        self.feat = np.zeros((self.n_items, self.d), dtype=np.float32);
        feat = pickle.load(open(content_file, 'rb'), encoding='latin1');
        if ss.issparse(feat):
            feat = feat.toarray();
        for trid in self.tr_iids:
            if trid in iids:
                self.feat[self.tr_iids[trid],:]=feat[iids[trid],:];
        print('Loading finished!');
        
    def build_graph(self):
        with tf.variable_scope('cbpr', reuse=tf.AUTO_REUSE):
            u  = tf.placeholder(tf.int32,   [None]);
            i  = tf.placeholder(tf.int32,   [None]);
            j  = tf.placeholder(tf.int32,   [None]);
            ic = tf.placeholder(tf.float32, [None, self.d]);
            jc = tf.placeholder(tf.float32, [None, self.d]);

            self.__ure = tf.get_variable(name="user_rating_embed",    shape=[self.n_users, self.k//2], dtype=tf.float32, initializer=tf.random_normal_initializer(0, 0.01));
            self.__uce = tf.get_variable(name="user_content_embed",   shape=[self.n_users, self.k//2], dtype=tf.float32, initializer=tf.random_normal_initializer(0, 0.01));
            self.__ire = tf.get_variable(name="item_rating_embed",    shape=[self.n_items, self.k//2], dtype=tf.float32, initializer=tf.random_normal_initializer(0, 0.01));
            self.__irb = tf.get_variable(name="item_rating_bias",     shape=[self.n_items,         1], dtype=tf.float32, initializer=tf.constant_initializer(0.0));
            self.__cem = tf.get_variable(name="content_embed_matrix", shape=[self.d, self.k//2],       dtype=tf.float32, initializer=tf.constant_initializer(2/(self.d*self.k))); 
            self.__icb = tf.get_variable(name="item_content_bias",    shape=[self.d,         1],       dtype=tf.float32, initializer=tf.constant_initializer(0.0));
            
        ureb = tf.nn.embedding_lookup(self.__ure, u);
        uceb = tf.nn.embedding_lookup(self.__uce, u);
        ireb = tf.nn.embedding_lookup(self.__ire, i);
        jreb = tf.nn.embedding_lookup(self.__ire, j);
        irbb = tf.nn.embedding_lookup(self.__irb, i);
        jrbb = tf.nn.embedding_lookup(self.__irb, j);
        iceb = tf.matmul(ic, self.__cem);
        jceb = tf.matmul(jc, self.__cem);

        x_ui  = tf.reduce_sum(tf.multiply(ureb, ireb)+tf.multiply(uceb, iceb), 1);
        x_uj  = tf.reduce_sum(tf.multiply(ureb, jreb)+tf.multiply(uceb, jceb), 1);
        x_uij = irbb - jrbb + x_ui - x_uj + tf.matmul(ic-jc, self.__icb);
        with tf.name_scope('output'):
            self.predc = tf.matmul(uceb, tf.transpose(iceb))+tf.matmul(ic, self.__icb);
            self.preda = self.predc + tf.matmul(ureb, tf.transpose(ireb)) + irbb
            if self.mode == 'l2':
                self.obj = tf.reduce_sum(tf.log(1+tf.exp(-x_uij)))+\
                           0.5*tf.reduce_sum(self.__cem**2)*self.le+\
                           0.5*tf.reduce_sum((ureb**2+uceb**2)*self.lu+ireb**2*self.li+jreb**2*self.lj)+\
                           0.5*(tf.reduce_sum(irbb**2+jrbb**2)+tf.reduce_sum(self.__icb**2))*self.lb;
            else:
                self.obj = tf.reduce_sum(tf.log(1+tf.exp(-x_uij)))+\
                           tf.reduce_sum(tf.abs(self.__cem))*self.le+\
                           tf.reduce_sum((tf.abs(ureb)+tf.abs(uceb))*self.lu+tf.abs(ireb)*self.li+tf.abs(jreb)*self.lj)+\
                           (tf.reduce_sum(tf.abs(irbb)+tf.abs(jrbb))+tf.reduce_sum(tf.abs(self.__icb)))*self.lb
        self.solver = tf.train.RMSPropOptimizer(self.lr).minimize(self.obj);
        return u, i, j, ic, jc;

    def model_training(self, model_path, sampling='user uniform', epochs=10, batch_size=256):
        with tf.Graph().as_default():
            u, i, j, ic, jc = self.build_graph();
            batch_limit = self.epoch_sample_limit//batch_size + 1;
            sess = tf.Session(config=self.tf_config);
            sampler = None;
            if sampling == 'user uniform':
                sampler = self._uniform_user_sampling;
            with sess.as_default():
                sess.run(tf.global_variables_initializer());
                print ('Training parameters: lu=%.6f, li=%.6f, lj=%.6f, lb=%.6f'%(self.lu, self.li, self.lj, self.lb));
                print ('Learning rate is %.6f, regularization mode is %s'%(self.lr, self.mode));
                print ('Training for %d epochs of %d batches using %s sampler'%(epochs, batch_limit, sampling));
                for eid in range(epochs):
                    total_time = 0;
                    bno = 1;
                    for ub, ib, jb in sampler(batch_size):
                        t1 = time.time();
                        _, loss = sess.run([self.solver, self.obj], feed_dict={u:ub, i:ib, j:jb, ic:self.feat[ib,:], jc:self.feat[jb,:]});
                        t2 = time.time()-t1;
                        sys.stderr.write('\rEpoch=%3d, batch=%6d, loss=%8.2f, time=%4.4fs'%(eid+1, bno, loss, t2));
                        total_time += t2;
                        bno += 1;
                        if bno == batch_limit:
                            break;
                    sys.stderr.write(' ... total time collapse %10.4fs'%(total_time));
                    sys.stderr.flush();
                    print();
            del self.feat;
            if os.path.exists(os.path.dirname(model_path)):
                print ('Saving model to path %s'%(model_path))
                saver = tf.train.Saver();
                saver.save(sess, model_path);
